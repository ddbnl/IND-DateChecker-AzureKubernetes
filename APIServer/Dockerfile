FROM ubuntu:latest

RUN apt-get update && apt-get -y install python3 python3-pip
RUN mkdir -p /api_server
RUN python3 -m pip install flask python-dateutil requests azure-storage-queue azure-data-tables

EXPOSE 5001 5001

COPY Common.py /api_server/Common.py
COPY APIServer.py /api_server/APIServer.py

WORKDIR /api_server
ENTRYPOINT ["python3", "/api_server/APIServer.py"]
