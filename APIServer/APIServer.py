from opencensus.ext.azure.log_exporter import AzureLogHandler
from opencensus.ext.flask.flask_middleware import FlaskMiddleware
from opencensus.trace import config_integration
from opencensus.trace.samplers import ProbabilitySampler
from opencensus.ext.azure.trace_exporter import AzureExporter
from opencensus.trace.tracer import Tracer
import logging
import azure.core.exceptions
from flask import Flask, request
from azure.storage.queue import QueueClient
from azure.data.tables import TableServiceClient
import datetime
import time
import json
import uuid
from Common import connect_str, queue_name, table_name, instrumentation_key

guid = str(uuid.uuid4())
FORMAT = '[%(asctime)s] [API-SERVER] [{}] %(message)s'.format(guid)
config_integration.trace_integrations(['logging', 'requests'])
tracer = Tracer(exporter=AzureExporter(connection_string=instrumentation_key), sampler=ProbabilitySampler(1.0))
logging.basicConfig(format=FORMAT)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(AzureLogHandler(connection_string=instrumentation_key))

app = Flask(__name__)
middleware = FlaskMiddleware(app,exporter=AzureExporter(connection_string=instrumentation_key),
                             sampler=ProbabilitySampler(rate=1.0),)

run_once_queue_client = QueueClient.from_connection_string(connect_str, queue_name)
table_service = TableServiceClient.from_connection_string(conn_str=connect_str)
table_client = table_service.get_table_client(table_name=table_name)


def send_message_to_queue(message):

    logger.info("Sending run once job to queue: {}".format(message))
    run_once_queue_client.send_message(message)


@app.route("/run_once", methods=['POST'])
def run_once():

    parameters = json.loads(request.json)
    run_id = uuid.uuid4()
    message = u"{},{},{},{},0".format(run_id, parameters['start_date'], parameters['end_date'],
                                    '+'.join(parameters['desks']))
    send_message_to_queue(message=message)
    return str(run_id)


@app.route("/run_continuous", methods=['POST'])
def run_continuous():

    parameters = json.loads(request.json)
    job_id = str(uuid.uuid4())
    entity = {
        'PartitionKey': 'ContinuousRun',
        'RowKey': job_id,
        'StartDate': parameters['start_date'],
        'EndDate': parameters['end_date'],
        'Desks': '+'.join(parameters['desks']),
        'Email': parameters['email'] or 'none',
        'LastRun': '',
        'ErrorCount': 0}
    table_client.create_entity(entity)
    logger.info("Sending continuous run job to database: {}".format(entity))
    return job_id


@app.route("/desks", methods=['GET'])
def desks():

    try:
        entity = table_client.get_entity('Desks', '0')
        if not datetime.datetime.now() - datetime.datetime.strptime(entity['CheckedAt'], '%d/%m/%Y %H:%M:%S') < \
           datetime.timedelta(minutes=60):
            logger.info("Desk data expired: {}".format(entity))
            table_client.delete_entity(partition_key='Desks', row_key='0')
            raise ValueError("Desks checked too long ago")
        desks = entity['Desks']
    except (azure.core.exceptions.ResourceNotFoundError, ValueError):
        send_message_to_queue(message="check_desks, 0")
        logger.info("Sending check desks job to queue")
        desks = _wait_for_desk_result()
    return desks


def _wait_for_desk_result():

    while True:
        timer = 1
        try:
            if timer > 120:
                return
            return table_client.get_entity('Desks', '0')['Desks']
        except azure.core.exceptions.ResourceNotFoundError:
            timer += 1
            time.sleep(1)
            continue


@app.route('/get_result')
def get_result():

    run_id = request.args['run_id']
    try:
        entity = table_client.get_entity('Result', run_id)
        return entity['Result']
    except azure.core.exceptions.ResourceNotFoundError:
        return ''


if __name__ == '__main__':

    app.run(host='0.0.0.0', port=5001)
