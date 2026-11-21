"""Read-only diagnosis for grading URLs; never logs tokens or edits remote servers.

Usage: python -X utf8 diagnose_submit.py https://example.trycloudflare.com/ask
Requires the local runtime, network, and a responding live grading server.
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

from check import validate_response


def diagnose(url):
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.path.rstrip('/')!='/ask':
        raise ValueError('Expected an absolute http(s) URL ending in /ask')
    home=f"{parsed.scheme}://{parsed.netloc}/"
    print('GET /health:',end=' ')
    try:
        h=requests.get(home+'health',timeout=(5,10))
        print(h.status_code,h.json())
    except Exception as exc:
        print(type(exc).__name__,str(exc)[:130])
    queries=[
        ('S2','서울시 금연구역에서 담배를 피우면 과태료가 얼마인가요?'),
        ('T1','서울시에서 자전거 탈 때 헬멧을 안 쓰면 과태료가 얼마인가요?')
    ]
    def call(pair):
        label,q=pair
        start=time.monotonic()
        try:
            r=requests.post(url,json={'question':q},timeout=(5,90))
            data=r.json()
            return label,r.status_code,round(time.monotonic()-start,2),data,validate_response(data)
        except Exception as exc:
            return label,None,round(time.monotonic()-start,2),{},[type(exc).__name__+': '+str(exc)[:150]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for label,status,elapsed,payload,issues in pool.map(call,queries):
            print('\n',label,'status=',status,'seconds=',elapsed,'issues=',issues)
            print(json.dumps(payload,ensure_ascii=False,indent=2))
    print('\n※ 이 도구는 근거 문항의 법률적 정답을 확정하지 않습니다. 전 문항 검사는 check.py --url 로 실행하세요.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('url',help='Deployed /ask URL')
    diagnose(parser.parse_args().url)
