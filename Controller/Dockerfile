FROM ubuntu:latest

RUN apt-get update && apt-get -y install python3 python3-pip
RUN mkdir -p /controller
RUN python3 -m pip install flask requests azure-storage-queue azure-data-tables opencensus-ext-azure opencensus-ext-logging opencensus-ext-flask opencensus-ext-requests
RUN python3 -m pip install markupsafe==2.0.1

EXPOSE 5002 5002

COPY Common.py /controller/Common.py
COPY Controller.py /controller/Controller.py

WORKDIR /controller
ENTRYPOINT ["python3", "/controller/Controller.py"]
