# IND DateChecker

Test app (checks available dates on IND date planner websites using Selenium) running in an AKS cluster.
Can handle single date check requests (check if a date is available in the specified period only once) or continuous checks (run repeatedly until a date in the specified period is found).

Components:
- AKS:
  - Azure public load balancer
    - Exposes Web frontend pod(s) 
  - Cluster IP services:
    - For internal DNS name to API server
  - Pod deployments:
    - Web frontend container
      - Runs FLASK to present front end
    - API container
      - Runs Flask to accept http requests
      - Handles making new date check requests, checking for results of previous checks, etc.
    - Worker container
      - Uses Selenium to check for dates  
      - Checks message queue for single run requests, executes them and writes result to database
      - Checks storage table for continuous run requests, executes them, stores result in database if a date is found, else runs again periodically
  - HPA for each deployment for auto scaling
- Azure storage account:
  - Queue:
    - Messages requesting a single date check
  - Table:
    - Entities for continuous date checks (i.e. run repeatedly until an available date is found) 
    - Entities for result of previous runs 
