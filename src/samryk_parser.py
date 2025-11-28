import time
import logging
import json
import os
from bs4 import BeautifulSoup
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
# ------------------------------------------------------------------
from selenium.webdriver.firefox.options import Options as FirefoxOptions
# ------------------------------------------------------------------
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium import webdriver
from selenium.webdriver.common.by import By


def get_driver():
    # 1. Initialize Firefox Options
    firefox_options = FirefoxOptions()

    firefox_options.add_argument("-private") # Equivalent to --incognito/private mode

    # 2. Configure Profile Preferences (Prefs)
    download_dir = 'sk_downloads'
    firefox_options.set_preference("browser.download.folderList", 2) # 0=desktop, 1=downloads, 2=custom
    firefox_options.set_preference("browser.download.dir", download_dir)
    firefox_options.set_preference("browser.download.useDownloadDir", True)
    firefox_options.set_preference("browser.download.manager.showWhenStarting", False)
    firefox_options.set_preference("pdfjs.disabled", True) # Disable built-in PDF viewer

    firefox_options.set_preference("permissions.default.image", 2) # 1=Allow, 2=Block (This may not work universally depending on Firefox version/setup)
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0"
    firefox_options.set_preference("general.useragent.override", user_agent)

    driver = webdriver.Firefox(options=firefox_options)


def get_text(element):
    return element.get_text(strip=True) if element else None


def parse_advert_data(driver):
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    soup = soup.find('div', class_='modal-content')    
    # --- Основные данные (Заголовок, Даты, Заказчик и т.д.) ---
    data = {
        "Заголовок": get_text(soup.find('div', class_='m-modal__title')),
        "Заказчик": None,
        "МЕТОД ЗАКУПКИ": None,
        "Приоритет": None,
        "Общая сумма лотов": None,
        "Электронная почта": None,
        "Телефон": None,
        "Внутренний номер": None,
        "Начало приема заявок": None,
        "Конец приема заявок": None
    }

    # Парсинг дат
    dates = soup.find_all('div', class_='m-rangebox__date')
    if len(dates) >= 2:
        data["Начало приема заявок"] = get_text(dates[0])
        data["Конец приема заявок"] = get_text(dates[1])

    # Парсинг полей по названию заголовка
    for key in data.keys():
        if data[key] is None:
            label = soup.find(lambda tag: tag.name == 'div' and 'm-infoblock__title' in tag.get('class', []) and key in tag.get_text())
            if label:
                # Текст лежит в родительском блоке, удаляем сам заголовок
                full_text = get_text(label.parent)
                label_text = get_text(label)
                data[key] = full_text.replace(label_text, '').strip()

    # --- Парсинг ЛОТОВ (исправленная часть) ---
    lots = []
    accordions = soup.find_all('div', class_='m-accordion')

    for acc in accordions:
        header = acc.find('div', class_='m-accordion__header')
        if not header: continue
        
        lot = {}
        
        # 1. Номер лота (пример: "1 (2074-1 Т, 4221402)")
        lot['Номер'] = get_text(header.find('label', class_='m-label'))
        
        # 2. Наименование и описание (лежит в колонке w-15)
        # Наименование внутри span m-span--big
        lot['Наименование'] = get_text(header.find('span', class_='m-span--big'))
        # Краткая характеристика лежит рядом
        desc_tag = header.find('div', class_='m-accordion__col w-15')
        if desc_tag:
            # Ищем div с классом m-accordion__title внутри этой колонки, который НЕ является заголовком столбца
            desc_title = desc_tag.find('div', class_='m-accordion__title') 
            lot['Характеристика'] = get_text(desc_title)

        # 3. Функция для извлечения значений по заголовку столбца (Количество, Ед. изм, Место, Сроки)
        def get_col_value(header_element, title_text):
            title_div = header_element.find(lambda t: t.name == 'div' and 
                                                    'm-accordion__title' in t.get('class', []) and 
                                                    title_text in t.get_text())
            if title_div:
                val_div = title_div.find_next_sibling('div', class_='m-accordion__description')
                return get_text(val_div)
            return None

        lot['Количество'] = get_col_value(header, 'Количество')
        lot['Ед. измерения'] = get_col_value(header, 'Ед. измерения') # Или 'mkeiShort' если поиск по классу
        lot['Место поставки'] = get_col_value(header, 'МЕСТО ПОСТАВКИ')
        lot['Сроки'] = get_col_value(header, 'СРОКИ')

        # 4. Цена и Сумма (особый случай, два значения в одной ячейке)
        price_title = header.find(lambda t: t.name == 'div' and 'Цена за ед./Сумма' in t.get_text())
        
        if price_title:
            # Надежный способ: берем родительскую колонку, и внутри неё ищем описание
            parent_col = price_title.parent
            val_div = parent_col.find('div', class_='m-accordion__description')
            
            # Проверяем, что val_div действительно найден, прежде чем искать span
            if val_div:
                spans = val_div.find_all('span')
                # Фильтруем пустые спаны, если вдруг попадутся
                valid_spans = [s for s in spans if get_text(s)]
                
                if len(valid_spans) >= 2:
                    lot['Цена за ед.'] = get_text(valid_spans[0])
                    lot['Сумма'] = get_text(valid_spans[1])
                elif len(valid_spans) == 1:
                    # На случай если верстка поплывет и будет только 1 цена
                    lot['Сумма'] = get_text(valid_spans[0])
        
        lots.append(lot)

    data['Лоты'] = lots

    return data


def parse_advert_data_and_download_techspec(driver, advert_id):
    
    TIMEOUT = 10

    base_url = "https://zakup.sk.kz/#/ext(popup:item/{advert_id}/advert)"
    url = base_url.format(advert_id=advert_id)
    driver.get(url)
    time.sleep(1)
    print('parsing advert data')
    advert_data = parse_advert_data(driver)

    print('parsing advert files')
    PARENT_CONTAINER = (By.CSS_SELECTOR, 'div.m-modal__body.ng-star-inserted')
    WebDriverWait(driver, TIMEOUT).until(
        EC.presence_of_element_located(PARENT_CONTAINER)
    )

    elements_to_click = driver.find_elements(
        By.XPATH, 
        "//div[@class='m-modal__body ng-star-inserted']//span[contains(@class, 'link--active')]"
    )

    for element in elements_to_click:
        element.click()
        NEW_ELEMENT_LOCATOR = (By.CSS_SELECTOR, 'div.d-flex.fileLink')
        container_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(NEW_ELEMENT_LOCATOR)
        )
        anchor_to_click = container_element.find_element(By.TAG_NAME, 'a')
        anchor_to_click.click()
        time.sleep(2)
    
    return advert_data


# main function
lots_ids = []
driver = get_driver()

for page_i in range(1, 62):
    print(f'Parsing page {page_i}')
    driver.get(
        f'https://zakup.sk.kz/#/ext?tabs=advert&pfrom=10000000&adst=PUBLISHED&lst=PUBLISHED&page={page_i}'
    )
    time.sleep(2)
    page_source = driver.page_source
    soup = BeautifulSoup(page_source, 'html.parser')
    lots_ids_on_page = []
    lots_on_page = soup.find_all('div', class_='m-sidebar__layout m-sidebar__layout--found-item ng-star-inserted')
    for lot in lots_on_page:
        lot_id = lot.find('div', class_='m-found-item__num ng-star-inserted').get_text(strip=True)
        lots_ids_on_page.append(lot_id)
    # lots_on_page[0].find('div', class_='m-found-item__num ng-star-inserted').get_text(strip=True)
    lots_ids.extend(lots_ids_on_page)

tender_ids_clean = [lot.strip('№ ') for lot in lots_ids]

# parse by id
result = parse_advert_data_and_download_techspec(driver, 1164921)