FROM ubuntu:latest

RUN apt-get update && apt-get -y install python3 python3-pip
RUN mkdir -p /api_server
RUN python3 -m pip install flask azure-storage-queue azure-data-tables opencensus-ext-azure opencensus-ext-logging opencensus-ext-flask opencensus-ext-requests
RUN python3 -m pip install markupsafe==2.0.1

EXPOSE 5001 5001

COPY Common.py /api_server/Common.py
COPY APIServer.py /api_server/APIServer.py

WORKDIR /api_server
ENTRYPOINT ["python3", "/api_server/APIServer.py"]
