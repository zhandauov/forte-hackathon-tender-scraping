import time
import requests
from bs4 import BeautifulSoup
import os
import re
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'Origin': 'https://goszakup.gov.kz',
    'Referer': 'https://goszakup.gov.kz/',
    # 'X-Auth-Token': '86bd9bffb7ce4c7d922ec04979af43a4'
}


def get_access_token(headers):
    auth_data = {"client_id": "widget-aiis-epp"}
    auth_url = 'https://help.ecc.kz/bridge/auth'
    auth_response = requests.post(auth_url, headers=headers, json=auth_data)
    auth_token = auth_response.json()['creds']['auth_token']

    session_token_url = f"https://help.ecc.kz/bridge/session?auth_token={auth_token}"
    response = requests.post(session_token_url, headers=headers)
    return response.json()['data']['access_token']


def send_request(session, url: str, method: str = 'get', retries: int = 3, *args, **kwargs):
    action = getattr(session, method, None)
    if action is None:
        raise ValueError(f"Unknown HTTP method: {method}")

    for attempt in range(retries + 1):
        try:
            return action(url, *args, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as err:
            if attempt == retries:
                raise
            time.sleep(0.1)


def extract_data_from_advert(lot_html):
    url = lot_html.find_all('td')[1].find('a')['href']
    id = int(url.split('/')[-1])
    return {
        "id": id,
        "url": url,
    }

def safe_int_convert(string):
    try:
        return int(string)
    except:
        return None

def get_max_adverts_count(session, base_url):    
    response = send_request(base_url.format(page_num=1))
    soup = BeautifulSoup(response.content, 'html.parser')
    all_adverts_on_page = soup.find('table', {'id': 'search-result'}).find('tbody').find_all('tr')
    max_adverts = [safe_int_convert(i) for i in soup.find('div', {'class': 'dataTables_info'}).text.split(' ') if safe_int_convert(i) is not None][-1]
    return max_adverts

def get_lots_basic_info(session, base_url):
    max_adverts = get_max_adverts_count(base_url)

    cur_page = 1

    offset = 500
    max_pages = max_adverts // offset + 1
    
    result = []

    while cur_page <= max_pages:

        print(f'parsing {cur_page} out of {max_pages}')
        
        # response = session.get(url.format(page_num=cur_page), headers=headers)
        response = send_request(session, base_url.format(page_num=cur_page), method='get')
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        if soup:
            all_lots_on_page = soup.find('table', {'id': 'search-result'}).find('tbody').find_all('tr')
        
        for lot_html in all_lots_on_page:
            result.append(extract_data_from_advert(lot_html))
        
        cur_page += 1
        time.sleep(2)

    session.close()
    return result

def parse_advert_view_info(advert_view_info):
        # 2. Словарь для перевода русских названий полей на английский
        # Используем короткие и понятные названия
    translation_map = {
        'Номер объявления': 'tender_id',
        'Наименование объявления': 'tender_name',
        'Статус объявления': 'tender_status',
        'Дата публикации объявления': 'publication_date',
        'Срок начала приема заявок': 'application_start_date',
        'Срок окончания приема заявок': 'application_end_date'
    }
    form_groups = advert_view_info.find_all('div', class_='form-group')
    parsed_data = {}
    for group in form_groups:
        label_tag = group.find('label', class_='control-label')
        input_tag = group.find('input', class_='form-control')

        if label_tag and input_tag:
            # Очищаем текст метки от лишних пробелов и переводим
            russian_key = label_tag.get_text(strip=True)
            english_key = translation_map.get(russian_key, russian_key.lower().replace(' ', '_'))

            # Получаем значение из атрибута 'value'
            value = input_tag.get('value')

            parsed_data[english_key] = value

    return parsed_data

def parse_obshie_svedeniya(general_info):
    # 2. Словарь для перевода русских названий полей
    translation_map = {
        'Способ проведения закупки': 'procurement_method',
        'Тип закупки': 'procurement_type',
        'Вид предмета закупок': 'subject_type',
        'Организатор': 'organizer_name',
        'Юр. адрес организатора': 'organizer_legal_address',
        'Кол-во лотов в объявлении': 'lot_count',
        'Сумма закупки': 'procurement_amount',
        'Признаки': 'attributes'
    }

    parsed_data = {}

    # 4. Итерация по строкам (tr) таблицы
    for row in general_info.find_all('tr'):
        # Находим заголовки (th) и данные (td)
        header = row.find('th')
        data = row.find('td')

        if header and data:
            # Очищаем и переводим заголовок
            russian_key = header.get_text(strip=True)
            english_key = translation_map.get(russian_key, russian_key.lower().replace(' ', '_'))

            # Для поля "Признаки" извлекаем текст из ul/li
            if english_key == 'attributes':
                # Находим все li элементы и объединяем их текст
                li_items = data.find_all('li')
                value = [li.get_text(strip=True) for li in li_items]
                if not value and data.get_text(strip=True):
                    value = [data.get_text(strip=True)] # Fallback
                elif not value:
                    value = None
            else:
                # Для остальных полей просто берем текст
                value = data.get_text(strip=True)

            parsed_data[english_key] = value
    
    return parsed_data

def parse_organizator_info(organizator_info):
    translation_map = {
        'ФИО представителя': 'representative_name',
        'Должность': 'position',
        'E-Mail': 'email'
    }

    parsed_data = {}

    for row in organizator_info.find_all('tr'):
        header = row.find('th')
        data = row.find('td')

        if header and data:
            # Очищаем и переводим заголовок
            russian_key = header.get_text(strip=True)
            english_key = translation_map.get(russian_key, russian_key.lower().replace(' ', '_'))

            # Получаем значение
            value = data.get_text(strip=True)

            parsed_data[english_key] = value

    # 5. Вывод результата
    return parsed_data

def parse_lots(soup):
    # 2. Словарь для перевода русских названий полей
    translation_map = {
        '№ п/п': 'seq_num',
        'Номер лота': 'lot_number',
        'Заказчик': 'customer',
        'Наименование': 'item_name',
        'Дополнительная характеристика': 'additional_specs',
        'Цена за ед.': 'unit_price',
        'Кол-во': 'quantity',
        'Ед. изм.': 'unit_of_measure',
        'Плановая сумма': 'planned_amount',
        'Сумма 1 год': 'amount_year_1',
        'Сумма 2 год': 'amount_year_2',
        'Сумма 3 год': 'amount_year_3',
        'Статус лота': 'lot_status',
        'Пред. план': 'prev_plan'
    }

    # 3. Находим таблицу и заголовки
    table = soup.find('table')
    headers = [th.get_text(strip=True) for th in table.find('tr').find_all('th')]
    english_headers = [translation_map.get(h, h.lower().replace(' ', '_')) for h in headers]

    parsed_lots = []

    # 4. Находим строки данных (пропускаем первую строку с заголовками)
    data_rows = table.find_all('tr')[1:]

    # 5. Итерация по строкам данных
    for row in data_rows:
        lot_data = {}
        # Находим все ячейки данных (td)
        cells = row.find_all('td')

        # Итерация по ячейкам и сопоставление с заголовками
        for i, cell in enumerate(cells):
            key = english_headers[i]
            value = cell.get_text(strip=True)

            # Обработка специального случая: Номер лота (извлекаем атрибут data-lot-id)
            if key == 'lot_number':
                anchor_tag = cell.find('a', class_='btn-select-lot')
                if anchor_tag:
                    lot_data['lot_id'] = anchor_tag.get('data-lot-id')
                # Используем очищенный текст для самого номера
                lot_data[key] = value

            # Обработка специального случая: Пред. план (проверяем наличие disabled input)
            elif key == 'prev_plan':
                input_tag = cell.find('input', disabled=True, type='checkbox')
                lot_data[key] = True if input_tag else False
            else:
                lot_data[key] = value

        parsed_lots.append(lot_data)

    return parsed_lots

def parse_techspec_id(session, docs_soup):

    REGEX = r'actionModalShowFiles\((.*)\)'

    all_docs = docs_soup.find('table').find_all('tr')
    for doc in all_docs:
        all_values = doc.find_all('td')

        if not all_values:
            continue

        title = all_values[0].get_text(strip=True)

        if 'техн' not in title.lower() or 'спецификац' not in title.lower():  # looking for technical specifications
            continue
        
        button = doc.find('button')
        
        if not button:
            continue

        onclick_value = button.get('onclick', '')
        match = re.search(REGEX, onclick_value)
        action_params = None
        if match:
            # Извлекаем содержимое скобок (группа 1)
            techspec_id = match.group(1)
            break
    
    params = techspec_id.split(',')

    response = send_request(
        session, 
        f'https://goszakup.gov.kz/ru/announce/actionAjaxModalShowFiles/{params[0]}/{params[1]}'
    )

    response.raise_for_status()
    files_soup = BeautifulSoup(response.content, 'html.parser')
    all_rows_in_files_specs = files_soup.find_all('tr')
    file_links = []
    for row_file_spec in all_rows_in_files_specs:
        cols = row_file_spec.find_all('td')
        if not cols:
            continue
        file_links.append(
            {
                'lot_id': params[0],
                'file_link': cols[1].find('a')['href'],
                'file_name': cols[1].find('a').get_text(strip=True)
            }
        )
        
    return file_links 


def download_techspec_files(session, files_info, save_dir='techspec_files_download'):
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    for item in files_info:
        url = item['file_link']
        
        filename = item['file_name']
        filepath = os.path.join(save_dir, filename)

        response = send_request(session, url, method='get')
        response.raise_for_status()
        with open(filepath, 'wb') as f:
            f.write(response.content)

        logger.info(f'Saved to {filepath}')


def get_full_zakup_info(session, zakup_id):
    base_url = f'https://goszakup.gov.kz/ru/announce/index/{zakup_id}' # 15746632
    general_url = base_url + '?tab=general'
    lots_url = base_url + '?tab=lots'
    docs_url = base_url + '?tab=documents'
    winners_url = base_url + '?tab=winners'

    # ----------------------------- #
    # print('parsing main info')
    time.sleep(1)
    response = send_request(session, general_url, method='get')
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')

    applications_count = soup.find('label', class_='label label-info')
    if applications_count is None:
        applications_count = 0
    else:
        applications_count = applications_count.get_text(strip=True)
        applications_count = int(applications_count.split(':')[-1])

    advert_view_info_soup = soup.find_all('div', class_='panel-body')[0]
    
    general_info = soup.find_all('div', class_='panel-body')[1]
    advert_view_info = parse_advert_view_info(advert_view_info_soup)
    organizator_info = parse_organizator_info(general_info.find_all('table')[1])
    obshie_svedeniya = parse_obshie_svedeniya(general_info.find_all('table')[0])
    
    # ----------------------------- #
    # print('parsing lots info')
    time.sleep(1)
    response = send_request(session, lots_url, method='get')
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser').find('div', class_='table-responsive')
    lots_info = parse_lots(soup)

    # ----------------------------- #
    # print('parsing techspecs')
    time.sleep(1)
    response = send_request(session, docs_url, method='get')
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    techspec_files = parse_techspec_id(session, soup)

    # ----------------------------- #
    nav_tabs = [i.get_text(strip=True).lower() for i in soup.find('ul', class_='nav nav-tabs').find_all('li')]

    result = {
        **advert_view_info,
        'applications_count': applications_count,
        **obshie_svedeniya,
        **organizator_info,
        'lots_info': lots_info,
        'techspec_files': techspec_files,
    }
    result['organizer_bin'] = result['organizer_name'].split(' ')[0]
    return result
