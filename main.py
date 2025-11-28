from requests import Session
from concurrent.futures import ThreadPoolExecutor
import requests
import os
import logging
from llm_prompts.prompts import affiliate_prompt, prompt_for_parsing_techspec
from src.utils import clear_folder
from src.openai_client import upload_files, get_response_from_gpt, get_client
from src.goszakup_parser import get_access_token, get_full_zakup_info, download_techspec_files, headers


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

techspec_folder = os.path.join("downloads", "goskazup_techspecs")

if __name__ == "__main__":
    token = get_access_token(headers)
    session_headers = headers.copy()
    session_headers['X-Auth-Token'] = token
    
    session = Session()
    session.headers.update(session_headers)

    advert_id_to_parse = 15755249

    logging.info('Собираю полную информацию по закупке')
    result = get_full_zakup_info(session, advert_id_to_parse)

    logging.info('Очищаю папку с техспецификациями')
    clear_folder(techspec_folder)

    logging.info('Загружаю техспецификации')
    download_techspec_files(session, result['techspec_files'], techspec_folder)

    files = os.listdir(techspec_folder)

    if len(files) > 3:
        raise Exception('Too many files to upload!')

    techspec_file_ids_in_openai = []
    client = get_client()
    for i, file_name in enumerate(files):
        logging.info(f'Выгружаю файл в OpenAI. ID {i}. Название файла: {file_name}')
        upload_metadata = upload_files(client, os.path.join(techspec_folder, file_name))
        techspec_file_ids_in_openai.append(upload_metadata)
    
    MODEL="gpt-5-nano"

    # OPENAI
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

        for future in futures:
            key = futures[future]
            try:
                result[key] = future.result()
            except Exception as e:
                result[key] = f"Error: {e}"

    # save json
    import json
    os.makedirs('reports', exist_ok=True)
    report_path = os.path.join('reports', f'goszakup_{advert_id_to_parse}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=4)
