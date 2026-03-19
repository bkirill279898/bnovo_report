import streamlit as st
import pandas as pd
import subprocess
import os
from datetime import datetime

st.set_page_config(page_title="Bnovo кассовое расписание", layout="wide")

st.title("Bnovo кассовое расписание")

# Путь к CSV файлу
CSV_FILE = 'dataprice/bnovo_bookings_latest.csv'

# Кнопка обновления
col1, col2 = st.columns([1, 4])
with col1:
    if st.button("Обновить данные", type="primary"):
        with st.spinner('Формируем отчет...'):
            try:
                # Запускаем скрипт формирования CSV
                result = subprocess.run(
                    ['python3', 'bnovo_bookings_report.py'],
                    capture_output=True,
                    text=True,
                    timeout=60
                )

                if result.returncode == 0:
                    st.success('✅ Данные успешно обновлены!')
                    st.rerun()  # Перезагружаем страницу
                else:
                    st.error(f'❌ Ошибка: {result.stderr}')
            except subprocess.TimeoutExpired:
                st.error('❌ Превышено время ожидания (60 сек)')
            except Exception as e:
                st.error(f'❌ Ошибка: {str(e)}')

# Показываем время последнего обновления
if os.path.exists(CSV_FILE):
    mod_time = os.path.getmtime(CSV_FILE)
    mod_datetime = datetime.fromtimestamp(mod_time)
    st.caption(f"Последнее обновление: {mod_datetime.strftime('%Y-%m-%d %H:%M:%S')}")

# Загружаем и показываем таблицу
try:
    df = pd.read_csv(CSV_FILE)

    # Интерактивная таблица
    st.dataframe(
        df,
        use_container_width=True,
        height=600
    )

except FileNotFoundError:
    st.warning("⚠️ Файл данных не найден. Нажмите 'Обновить данные' для создания отчета.")
except Exception as e:
    st.error(f"❌ Ошибка при загрузке данных: {str(e)}")