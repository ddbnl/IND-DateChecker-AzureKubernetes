FROM selenium/standalone-chrome

RUN sudo apt-get update && sudo apt-get -y install python3 python3-pip
RUN sudo mkdir -p /worker
RUN pip3 install selenium webdriver_manager

EXPOSE 5003 5003

COPY Worker.py /worker/Worker.py

WORKDIR /worker
ENTRYPOINT ["python3", "/worker/Worker.py"]
