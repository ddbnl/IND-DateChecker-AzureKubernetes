import uuid
from Common import connect_str, queue_name
from azure.storage.queue import QueueClient

run_once_queue_client = QueueClient.from_connection_string(connect_str, queue_name)

requests = 40
if __name__ == '__main__':
    for _ in range(0, requests):
        guid = str(uuid.uuid4())
        message = "{},23/03/2022,23/06/2022,IND Amsterdam+IND Den Haag,none".format(guid)
        run_once_queue_client.send_message(message)
