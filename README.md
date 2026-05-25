# OPERA AI — PC Local AI Agent

> 다운로드형 PC local 실행형 AI 에이전트 (리치)
> Windows / Mac 지원
> 구독형 결제 + workbot-ai 연동

## 개요

OPERA AI는 사용자의 PC에서 직접 마우스와 키보드를 움직이며 작업을 수행하는 AI 에이전트입니다.
XG5000 자동화, 화물 데이터 수집, PC 자동화 등 실제 업무를 대신 처리합니다.

## 아키텍처

```
[workbot-ai (VM)] ──WOL→ [OPERA AI (PC local)]
     │                           │
     ├─ 작업 지시                ├─ pyautogui 제어
     ├─ 결과 수신                ├─ XG5000 자동화
     └─ 텔레그램 보고            └─ 파일/앱/웹 자동화
```

## 시작하기

### 시스템 요구사항
- Windows 10/11 or macOS 12+
- Python 3.10+
- 안정적인 네트워크 (WOL 지원)

### 설치
- 상세 설치 가이드: /docs/INSTALL.md

## 라이선스
© 2026 WORKBOT AI. All rights reserved.

