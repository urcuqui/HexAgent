import concurrent.futures
import requests

URL = "http://192.168.80.22/api/reports/12/issue-token"

def issue_token():
    response = requests.post(URL)
    return response.json()

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    results = list(executor.map(lambda _: issue_token(), range(10)))

for result in results:
    print(result)