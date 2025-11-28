from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import threading
import json
import os
import logging
from requests import Session
from concurrent.futures import ThreadPoolExecutor
from llm_prompts.prompts import affiliate_prompt, prompt_for_parsing_techspec
from src.utils import clear_folder
from src.openai_client import upload_files, get_response_from_gpt, get_client
from src.goszakup_parser import get_access_token, get_full_zakup_info, download_techspec_files, headers

app = Flask(__name__)
CORS(app)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Глобальный словарь для хранения статусов задач
tasks_status = {}

techspec_folder = os.path.join("downloads", "goskazup_techspecs")

def parse_advert(advert_id):
    """Основная функция парсинга объявления"""
    task_id = str(advert_id)
    tasks_status[task_id] = {
        'status': 'running',
        'progress': 0,
        'message': 'Инициализация...',
        'result': None,
        'error': None
    }
    
    try:
        # Получаем токен
        tasks_status[task_id]['progress'] = 5
        tasks_status[task_id]['message'] = 'Получение токена доступа...'
        token = get_access_token(headers)
        session_headers = headers.copy()
        session_headers['X-Auth-Token'] = token
        
        session = Session()
        session.headers.update(session_headers)
        
        # Собираем информацию
        tasks_status[task_id]['progress'] = 15
        tasks_status[task_id]['message'] = 'Собираю полную информацию по закупке...'
        result = get_full_zakup_info(session, advert_id)
        
        # Очищаем папку
        tasks_status[task_id]['progress'] = 30
        tasks_status[task_id]['message'] = 'Очищаю папку с техспецификациями...'
        os.makedirs(techspec_folder, exist_ok=True)
        clear_folder(techspec_folder)
        
        # Загружаем техспецификации
        tasks_status[task_id]['progress'] = 40
        tasks_status[task_id]['message'] = 'Загружаю техспецификации...'
        download_techspec_files(session, result['techspec_files'], techspec_folder)
        
        files = os.listdir(techspec_folder)
        
        if len(files) > 3:
            raise Exception('Too many files to upload!')
        
        # Загружаем файлы в OpenAI
        tasks_status[task_id]['progress'] = 50
        tasks_status[task_id]['message'] = 'Загружаю файлы в OpenAI...'
        techspec_file_ids_in_openai = []
        client = get_client()
        
        for i, file_name in enumerate(files):
            progress = 50 + int((i + 1) / len(files) * 20)
            tasks_status[task_id]['progress'] = progress
            tasks_status[task_id]['message'] = f'Выгружаю файл в OpenAI ({i+1}/{len(files)}): {file_name}'
            upload_metadata = upload_files(client, os.path.join(techspec_folder, file_name))
            techspec_file_ids_in_openai.append(upload_metadata)
        
        # Анализ через GPT
        tasks_status[task_id]['progress'] = 70
        tasks_status[task_id]['message'] = 'Анализирую через GPT (техспецификация)...'
        MODEL = "gpt-5-nano"
        
        with ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(
                    get_response_from_gpt,
                    client=client,
                    input_text=prompt_for_parsing_techspec,
                    file_ids=techspec_file_ids_in_openai,
                    model=MODEL,
                    enable_web_search=False,
                    label="Techspec"
                ): "techspec_analyzed",
                executor.submit(
                    get_response_from_gpt,
                    client=client,
                    input_text=affiliate_prompt,
                    file_ids=techspec_file_ids_in_openai,
                    model=MODEL,
                    enable_web_search=False,
                    label="Affiliate"
                ): "affiliate_analysis"
            }
            
            completed = 0
            total = len(futures)
            for future in futures:
                key = futures[future]
                try:
                    tasks_status[task_id]['message'] = f'Анализирую через GPT ({key})...'
                    result[key] = future.result()
                    completed += 1
                    tasks_status[task_id]['progress'] = 70 + int(completed / total * 25)
                except Exception as e:
                    result[key] = f"Error: {e}"
        
        # Сохраняем результат
        tasks_status[task_id]['progress'] = 95
        tasks_status[task_id]['message'] = 'Сохраняю результат...'
        os.makedirs('reports', exist_ok=True)
        report_path = os.path.join('reports', f'goszakup_{advert_id}.json')
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=4)
        
        tasks_status[task_id]['status'] = 'completed'
        tasks_status[task_id]['progress'] = 100
        tasks_status[task_id]['message'] = 'Отчёт успешно сформирован!'
        tasks_status[task_id]['result'] = f'goszakup_{advert_id}.json'
        
    except Exception as e:
        logger.error(f"Error parsing advert {advert_id}: {e}", exc_info=True)
        tasks_status[task_id]['status'] = 'error'
        tasks_status[task_id]['error'] = str(e)
        tasks_status[task_id]['message'] = f'Ошибка: {str(e)}'


@app.route('/api/parse', methods=['POST'])
def start_parsing():
    """Запуск парсинга объявления"""
    data = request.json
    advert_id = data.get('advert_id')
    
    if not advert_id:
        return jsonify({'error': 'advert_id is required'}), 400
    
    try:
        advert_id = int(advert_id)
    except ValueError:
        return jsonify({'error': 'advert_id must be a number'}), 400
    
    task_id = str(advert_id)
    
    # Если задача уже выполняется, возвращаем её статус
    if task_id in tasks_status and tasks_status[task_id]['status'] == 'running':
        return jsonify({'task_id': task_id, 'message': 'Task already running'}), 200
    
    # Запускаем парсинг в отдельном потоке
    thread = threading.Thread(target=parse_advert, args=(advert_id,))
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'task_id': task_id,
        'message': 'Parsing started'
    }), 200


@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    """Получение статуса задачи"""
    if task_id not in tasks_status:
        return jsonify({'error': 'Task not found'}), 404
    
    return jsonify(tasks_status[task_id]), 200


@app.route('/api/reports', methods=['GET'])
def list_reports():
    """Список доступных отчётов"""
    reports_dir = 'reports'
    if not os.path.exists(reports_dir):
        return jsonify({'reports': []}), 200
    
    reports = [f for f in os.listdir(reports_dir) if f.endswith('.json')]
    return jsonify({'reports': reports}), 200


@app.route('/reports/<filename>')
def serve_report(filename):
    """Отдача JSON файлов из папки reports"""
    return send_from_directory('reports', filename)


@app.route('/')
def index():
    """Главная страница - отдаём HTML"""
    return send_from_directory('.', 'report_viewer.html')


if __name__ == '__main__':
    app.run(debug=True, port=5000)

