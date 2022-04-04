#!/bin/bash
cp Common.py ./Controller/Common.py
cp Common.py ./Worker/Common.py
cp Common.py ./APIServer/Common.py
cp Common.py ./WebFrontend/Common.py

docker build ./Controller -t controller
docker build ./APIServer -t api_server
docker build ./Worker -t worker
docker build ./WebFrontend -t web_frontend

pwsh ./build.ps1

docker tag controller indcr.azurecr.io/controller
docker tag api_server indcr.azurecr.io/api_server
docker tag worker indcr.azurecr.io/worker
docker tag web_frontend indcr.azurecr.io/web_frontend

docker push indcr.azurecr.io/controller
docker push indcr.azurecr.io/api_server
docker push indcr.azurecr.io/worker
docker push indcr.azurecr.io/web_frontend

kubectl delete -f ./CreateResources/aks.yml
kubectl create -f ./CreateResources/aks.yml
