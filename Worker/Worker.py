from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.by import By
from azure.storage.queue import QueueClient
from azure.data.tables import TableServiceClient
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
import datetime
import logging
logging.basicConfig(level=logging.INFO)

connect_str = ""
queue_run_once_client = QueueClient.from_connection_string(connect_str, 'run-once-queue')
table_service = TableServiceClient.from_connection_string(conn_str=connect_str)
table_client = table_service.get_table_client(table_name="INDTable")


class DateChecker:

    def __init__(self, url, email=None, desks=None):
        """
        Check available dates on website.
        """
        self.months = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september',
                       'oktober', 'november', 'december']
        self.url = url
        self.driver = None
        self.run_id = None
        self.desired_months = None
        self.desks = desks
        self.email = email
        self.results = None

    def _init_driver(self):
        """
        Start a headless chrome driver.
        """
        chrome_options = webdriver.ChromeOptions()
        chrome_options.headless = True
        self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    def loop(self):

        message_switch = True
        while True:
            if message_switch:
                self.run_from_message()
            else:
                self.run_from_database()
            message_switch = not message_switch
            time.sleep(1)

    def run_from_database(self):

        my_filter = "PartitionKey eq 'ContinuousRun'"
        entities = table_client.query_entities(my_filter)
        for entity in entities:
            if not entity['LastRun'] or \
                datetime.datetime.now() - datetime.datetime.strptime(entity['LastRun'], '%d/%m/%Y %H:%M:%S') > \
                    datetime.timedelta(minutes=5):
                entity['LastRun'] = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
                table_client.update_entity(entity=entity)
                self.run_id = entity['RowKey']
                start_date_obj = datetime.datetime.strptime(entity['StartDate'], "%d/%m/%Y")
                end_date_obj = datetime.datetime.strptime(entity['EndDate'], "%d/%m/%Y")
                self.desired_months = [self.months[i - 1] for i in range(start_date_obj.month, end_date_obj.month + 1)]
                self.desks = [desk.lower() for desk in entity['Desks'].split('+')]
                self.email = entity['Email'] if entity['Email'] != 'none' else ''
                self.check_available_dates(store_results=True)
                if self.results:
                    table_client.delete_entity(partition_key=entity['PartitionKey'], row_key=entity['RowKey'])
                return

    def run_from_message(self):

        message = queue_run_once_client.receive_message()
        if message:
            try:
                self.run_id, start_date, end_date, desks, email = message.content.split(',')
                start_date_obj = datetime.datetime.strptime(start_date, "%d/%m/%Y")
                end_date_obj = datetime.datetime.strptime(end_date, "%d/%m/%Y")
                self.desired_months = [self.months[i - 1] for i in range(start_date_obj.month, end_date_obj.month + 1)]
                self.desks = [desk.lower() for desk in desks.split('+')]
                self.email = email if email != 'none' else ''
                self.check_available_dates(store_results=True)
                queue_run_once_client.delete_message(message)
            except Exception as e:
                queue_run_once_client.delete_message(message)

    def get_desk_options(self):

        desk_dropdown = Select(self.driver.find_element(by=By.ID, value='desk'))
        return [desk.text for desk in desk_dropdown.options]

    def check_available_dates(self, store_results=False):
        """
        Check all available dates in desired months on url.
        :return:
        """
        self.results = []
        self._init_driver()
        self.driver.get(self.url)
        desk_dropdown = Select(self.driver.find_element(by=By.ID, value='desk'))
        click_month_picker = True
        if not self.desks:
            desk_values = desk_dropdown.options
        else:
            desk_values = [option for option in desk_dropdown.options if option.text.lower() in self.desks]
        for desk_value in desk_values:
            if not desk_value:
                continue
            click_month_picker = self._check_desk_for_available_date(desk_value=desk_value, desk_dropdown=desk_dropdown,
                                                                     click_month_picker=click_month_picker)
        self.driver.quit()
        if self.results:
            if self.email:
                self.mail_results()
            if store_results:
                self.store_results()

    def _click_month_picker(self):
        """
        Click month picker to reveal available months on website.
        """
        month_picker_element = self.driver.find_element(by=By.XPATH,
                                                        value="/html/body/app/div/div[2]/div/div/div/oap-appointment/div/div/oap-appointment-reservation/div/form/div[4]/available-date-picker/div/datepicker/datepicker-inner/div/daypicker/table/thead/tr[1]/th[2]/button")
        month_picker_element.click()
        time.sleep(2)

    def _check_desk_for_available_months(self, desk_value):
        """
        Check which months are available for the current desk. If any desired ones available, click the first and call
        func to find a day on that month.
        :param desk_value: str
        :return: True if any desired months available (Bool)
        """
        potential_month_buttons = self.driver.find_elements(by=By.CLASS_NAME, value="btn-default")
        for potential_month_button in potential_month_buttons:
            month_text = potential_month_button.text.lower()
            if month_text in self.desired_months:
                if not potential_month_button.is_enabled():
                    continue
                potential_month_button.click()
                time.sleep(2)
                self._check_month_for_available_date(desk_value=desk_value, month=month_text)
                return True

    def _check_month_for_available_date(self, desk_value, month):
        """
        A month has been selected, now check for any available days. If there are any, add the first one to results as
        we now have a desk, month and day.
        :param desk_value: str
        :param month: str
        """
        potential_day_buttons = self.driver.find_elements(by=By.CLASS_NAME, value="btn-sm")
        days_already_done = []
        for potential_day_button in potential_day_buttons:
            day_text = potential_day_button.text
            if not day_text.isdigit() or day_text in days_already_done:
                continue
            days_already_done.append(day_text)
            if not potential_day_button.is_enabled():
                continue
            self.results.append('{} - {} {}'.format(desk_value.text, day_text, month))
            logging.info('[{}] Found result: {}'.format(datetime.datetime.now(), desk_value.text))
            break
        else:
            logging.info('[{}] No result: {}'.format(datetime.datetime.now(), desk_value.text))

    def _check_desk_for_available_date(self, desk_value, desk_dropdown, click_month_picker=True):
        """
        Check a specific desk for an available month and day if any.
        :param desk_value:
        :param desk_dropdown:
        :param click_month_picker:
        :return: True if a month has been clicked to find a day (Bool)
        """
        desk_dropdown.select_by_visible_text(desk_value.text)
        time.sleep(2)
        logging.info('[{}] Do {}'.format(datetime.datetime.now(), desk_value.text))
        if click_month_picker:
            self._click_month_picker()
        if self._check_desk_for_available_months(desk_value=desk_value):
            return True

    def store_results(self):

        entity = {
            'PartitionKey': 'Result',
            'RowKey': self.run_id,
            'Result': ','.join(self.results)
        }
        table_client.create_entity(entity)

    def mail_results(self):
        """
        Email results using GMAIL SMTP.
        """
        mail_content = "\n".join(self.results)
        sender_address = ''
        sender_pass = ''

        message = MIMEMultipart()
        message['From'] = sender_address
        message['To'] = self.email
        message['Subject'] = 'IND Datum gevonden!'
        message.attach(MIMEText(mail_content, 'plain'))
        session = smtplib.SMTP('smtp.gmail.com', 587)
        session.starttls()
        session.login(sender_address, sender_pass)
        text = message.as_string()
        session.sendmail(sender_address, self.email, text)
        session.quit()


if __name__ == '__main__':

    date_checker = DateChecker(url='https://oap.ind.nl/oap/nl/#/doc')
    date_checker.loop()
