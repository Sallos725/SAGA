# SAGA — Stateful Agentic Game Architecture

**한국어** | [English](README.md)

[![CI (main)](https://github.com/Sallos725/SAGA/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Sallos725/SAGA/actions/workflows/ci.yml)
[![Python 3.11–3.13](docs/assets/python-versions.svg)](pyproject.toml)
[![개발 단계: 프로토타입](docs/assets/stage-prototype.svg)](docs/IMPLEMENTATION_PLAN.md)

장기간 이어지는 에이전트 기반 게임과 RisuAI를 위한, 결정론적이며 리플레이 가능한 TRPG 런타임입니다.

[빠른 시작](#빠른-시작) · [아키텍처](docs/ARCHITECTURE.md) · [로드맵](docs/IMPLEMENTATION_PLAN.md) · [규칙 세트](packs/rulesets/README.md) · [기여 안내](CONTRIBUTING.md)

프롬프트에 의존하는 챗봇을 하나 더 만드는 대신, 각 구성 요소의 책임을 명확히 나누는 것을 목표로 합니다.

- **런타임 / 게임 상태**는 HP, 인벤토리, 위치, 상태 이상, 퀘스트 상태, 진행 시계, 이벤트 순서 등 게임의 기준이 되는 정확한 사실을 관리합니다.
- **의미 기반 메모리**는 `MemoryPort` 뒤에 두는 선택 사항입니다. 메모리를 사용하지 않거나, 테스트용 가짜 어댑터, NMOS 또는 다른 구현으로 회상과 검색을 처리할 수 있습니다.
- **주사위 / 도구**는 계산과 외부에서 검증할 수 있는 도구 결과를 담당합니다.
- **LLM**은 허용된 범위 안에서 의도를 해석하고 이미 결정된 결과를 서술합니다. 게임의 기준 상태를 직접 변경하지 않습니다.
- **RisuAI**는 프런트엔드이자 카드·로어 전달 인터페이스이며, 게임 상태의 최종 기준은 아닙니다.
- **RuleCheeseBench**는 동일한 런타임을 재사용하여 리플레이 가능한 시나리오에서 모델을 비교합니다.

SAGA는 상태의 영속 저장, 리플레이, 분기, 외부 도구 결과의 영속 저장을 제공합니다.
PocketRisu 명령어 데모는 NMOS 없이도 안정적인 사용자 턴 식별자를 이 런타임에 연결하며,
Vertex 모델로 제한된 자연어 공격·능력 판정·내성을 해석하고, 판정 확정 뒤 서술을
선택적으로 붙일 수 있습니다. 실제 서비스용 NMOS, MCP와 일반 카드·로어 연동은 남아 있습니다.

## 설계 배경

SAGA는 Aethrion 같은 결정론적 RP 엔진의 핵심 원칙을 따릅니다. 재생성(reroll)으로 서술은 바꿀 수 있지만,
상태나 주사위 결과가 달라져서는 안 됩니다. 장기 메모리는 선택적으로 연결하는 외부 어댑터이며,
SAGA는 게임 엔진 안에 별도의 메모리 시스템을 내장하지 않습니다.

## 반드시 지켜야 할 불변 조건

1. **게임의 기준 상태는 메모리와 다릅니다.** 정확한 HP, 아이템 수량, 퀘스트 단계처럼 정확성이 필요한 값을 NMOS에 맡기지 않습니다.
2. **재생성은 도구를 두 번 실행하지 않습니다.** 주사위와 도구의 출력은 한 번만 저널에 기록하고, 이후에는 기록된 결과를 재사용합니다.
3. **확정된 모든 상태 변경은 이벤트까지 추적할 수 있어야 합니다.**
4. **핵심 규칙은 시스템 시각을 읽거나 I/O를 수행하지 않습니다.** 시간은 명시적인 이벤트로 전달합니다.
5. **LLM 출력을 신뢰할 수 있는 상태로 취급하지 않습니다.** 파싱한 의도는 디스패치 전에 허용 항목이 명시된 닫힌 스키마로 검증해야 합니다.
6. **메모리 장애는 제한된 기능 저하로 처리합니다.** 일시적인 NMOS 장애로 회상 품질은 낮아질 수 있지만, 게임 상태가 손상되어서는 안 됩니다.
7. **벤치마크 실행은 재현 가능해야 합니다.** 시나리오, 규칙 버전, 시드, 모델 설정, 이벤트 저널을 함께 기록합니다.

## 빠른 시작

API 키 없이 PocketRisu **6011** 플레이테스트를 실행하려면
[격리된 배포 안내](deploy/README.md)와 [시작 카드·프로바이더](packs/risu-starter/README.md)를 참고하세요.
`attack opponent`, `check strength 10`, `save dexterity 12` 명령을 보낼 수 있으며,
응답 재생성은 확정된 이벤트와 주사위 결과를 재사용합니다. SRD 일부 규칙을 사용하는
제한된 데모입니다. 명령과 기본 서술은 키 없이 동작합니다.
Gemini 자연어 행동 해석과 서술을 쓰려면 **설정 → 플러그인 → SAGA SRD Demo → Vertex 서비스 계정 JSON**에
JSON 전체를 붙여넣으세요. 메인 모델은 **SAGA SRD Demo**로 유지합니다. SAGA가 프로젝트와
global 엔드포인트를 읽고 **Gemini 3.8 Flash**로 자동 연결하므로 다른 모델 설정은 필요 없습니다.
비우면 명령과 기본 서술을 씁니다. 서술 실패 시 확정된 사실과 상태를 표시하며,
행동 해석 결과가 불확실하면 자동 재호출 없이 대기 오류를 반환합니다.
[설정과 인증정보 취급 범위](packs/risu-starter/README.md#optional-vertex-narration)를 참고하세요.
플러그인·카드 0.4.0과 프로토콜 3에서는 `상대를 공격한다`, `힘으로 난이도 10 판정을 한다`
같은 단일 자연어 행동을 지원합니다. 대상과 판정·내성의 DC는 명시해야 합니다. 새 행동은
해석 1회와 서술 1회, 재생성은 해석 없이 서술 최대 1회의 모델 호출이 발생할 수 있습니다.
자연어 문구를 수정하면 새 행동으로 구분합니다. 이 기능은 오프라인 테스트와 별도
플러그인→API→SQLite 통합 검증을 통과했습니다. 현재 6011에는 백엔드와 플러그인 0.4.0을
배포했고, `상대를 공격한다`와 재생성 1회에서 새 서술·동일 상태·주사위·저널·테이프를
확인했습니다. 기존 호스트 카드를 유지했으며 새 시작 카드는 가져오지 않았습니다.
능력 판정·내성과 일반적인 표현의 실제 모델 품질은 미검증입니다.
[제한된 실호출 검증 보고서](docs/progress/risu-natural-intent-live.md)를 참고하세요.
실제 청구액은 관측하지 않았습니다.
이전 서술 전용 버전의 승인된 실제 호출 2회는 성공했지만, 새 행동 해석과 실제 청구액을
검증한 결과는 아닙니다.

이 데모에서는 호스트 설정을 `useSayNothing=false`로 지정하세요. 댓글을 제외한 전체 기록은
숨겨진 사용자 턴을 포함해 256개까지 지원합니다. 가장 최근 행동의 의미를 수정하면 안전하게
분기하며, 이전 기록을 바꿨다면
처음으로 미확정 상태가 된 턴부터 다시 진행해야 합니다. 다른 메인 프로바이더를 선택할 때는
데모 플러그인을 비활성화하세요.

브리지 v2는 명령의 대소문자·공백만 바꿔도 같은 이벤트를 재사용하고, 숨겨진 턴의 확정 상태를
유지합니다. 기존 v1 채팅은 원본 기록 그대로 재생하는 읽기 전용이며, 새 행동은 새 채팅에서
시작해야 합니다. 클라이언트가 메시지 ID를 바꿀 수 있고 서버 시드도 알려져 있으므로 적대적인 조작까지 격리한
벤치마크 환경은 아닙니다. [식별자·숨김 처리 수정 보고서](docs/progress/risu-identity-context-fix.md)를 참고하세요.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
python -m pip install -e '.[dev,api]'
pytest
uvicorn saga_trpg.api.app:create_app --factory --reload --port 4850
```

서버를 실행한 뒤 다음 요청을 보내 보세요.

```bash
curl -s -X POST http://127.0.0.1:4850/worlds/demo/events \
  -H 'content-type: application/json' \
  -d '{"event_id":"turn-1","kind":"attack","actor_id":"player","payload":{"target_id":"goblin","attack_bonus":5,"target_ac":14,"damage":"1d8+3"}}'

curl -s http://127.0.0.1:4850/worlds/demo/state
```

디버그 API는 월드를 `./data/worlds.sqlite3`에 영속 저장합니다. 저장 디렉터리는 `TRPG_DATA_DIR`로
변경할 수 있습니다. 같은 이벤트 ID 또는 멱등성 키로 요청을 반복하면 기록된 결과를 반환합니다.
새 행동에는 새 식별자를 사용하세요. 재시작 후 복구, 스냅샷, 리플레이, 분기, 실패 시 동작은
[영속 저장 계약](docs/PERSISTENCE.md)을 참고하세요.

배포 패키지 이름은 `saga-trpg`이며, Python에서는 `saga_trpg`로 임포트합니다. 이전 체크아웃을
업데이트했다면 편집 가능 모드로 설치한 패키지를 다시 설치하세요. 이전 패키지 이름의 임포트 별칭은
제공하지 않습니다. 기존 저장소 v1 데이터베이스는 서버를 시작하기 전에 백업을 포함한 명시적
업그레이드가 필요합니다.

```bash
python -m saga_trpg.adapters.storage.migrations data/worlds.sqlite3 \
  --backup data/worlds.pre-v2.sqlite3
```

먼저 서버를 중지하고, 기존 파일과 겹치지 않는 새 백업 파일 이름을 지정하세요. 이미 v2인
데이터베이스는 변경하지 않으며, 알 수 없는 버전이면 작업을 거부합니다.
이벤트·상태 스키마 v1과 런타임의 의미 체계는 그대로 유지됩니다.

Python 3.11–3.13을 사용하고, 로컬에서도 CI와 동일한 검증 명령을 실행하세요.

```bash
pytest
ruff check .
ruff format --check .
python -m compileall src
```

GitHub Actions는 푸시와 풀 리퀘스트마다 세 Python 버전 모두에서 전체 테스트를 실행합니다.
프로바이더 테스트도 Node.js 22로 CI에서 실행합니다: `node --test tests/risu_provider.test.cjs`.

선택 가능한 규칙 시스템 예제로 **SRD 5.2.1**(CC BY 4.0)과 **Fate Condensed**(CC BY 3.0)를 제공합니다.
각 예제에는 타입이 정의된 행동 핸들러, 모듈 매니페스트, 공통 봇 설명, 시스템 프롬프트,
액터·행동 예제, 저작자 표시가 포함됩니다. 생성 명령과 정확한 지원 범위는
[규칙 세트 예제](packs/rulesets/README.md)를 참고하세요.
이들은 일부 기능을 구현한 런타임·소스 팩 예제이며, RisuAI의 기본 모듈 가져오기 기능과는 아직 연결되지 않았습니다.

## 프로젝트 구조

```text
src/saga_trpg/
  domain/        순수 데이터 모델
  runtime/       이벤트 디스패치, 저널, 리플레이, 결정론적 난수 생성
  ports/         메모리, 주사위, 서술, 의도 해석 인터페이스
  adapters/      NMOS, 로컬/MCP 주사위, RisuAI/OpenAI 호환 어댑터
  modules/       교체 가능한 규칙 세트
  benchmark/     RuleCheeseBench 실행 기반
  api/           얇은 HTTP 서버 계층

docs/
  ARCHITECTURE.md
  IMPLEMENTATION_PLAN.md
  AGENT_HANDOFF.md
  RULECHEESEBENCH_SPEC.md
  RISUAI_INTEGRATION.md
  adr/
```

## 현재 구현 상태

구현된 기능:

- 이벤트·상태 모델
- 월드 + 이벤트 + 주사위 키를 기준으로 하는 결정론적 주사위
- 인메모리·JSONL 저널과 트랜잭션을 지원하는 SQLite 월드 저장소
- 주기적 스냅샷, 검증된 리플레이, 분기 출처를 기록하는 독립적인 브랜치
- 이벤트 ID 또는 멱등성 키를 기준으로 영속 유지되는 멱등성
- 백업, 롤백, 리플레이 검증을 포함하는 명시적 저장소 v1 → v2 마이그레이션
- 범용 SQLite 도구 결과 테이프와 테이프에 기록된 외부 주사위 결과를 처리하는 저장소 경로
- 닫힌 행동 스키마와 버전 관리를 갖춘, 선택 가능한 SRD 5.2.1 및 Fate Condensed 예제 모듈
- 리플레이 가능한 범용 판타지 공격 규칙
- FastAPI 디버그 엔드포인트
- 호스트 턴을 불변 분기에 연결하는, 버전이 명시된 OpenAI 형태의 Risu completion 어댑터
- 메모리 없이 SRD 명령을 실행하고 확정 상태를 표시하는 PocketRisu v3 프로바이더와 시작 카드
- 기존 도구 테이프에 저장하는 선택적 Vertex 자연어 공격·능력 판정·내성 해석
- 같은 서비스 계정 JSON으로 사용하는 Vertex 서술, 확정 후 호출과 제한 시간·기본 서술 대체
- 미구현 부분의 계약을 TODO로 명시한 NMOS 및 MCP Dice 포트·어댑터
- 벤치마크 결과 모델과 시나리오 구조
- 핵심 불변 조건을 검증하는 테스트

아직 구현되지 않은 기능:

- 실제 서비스용 NMOS API 연동
- 실제 서비스용 MCP 클라이언트 연동
- 자연어 능력 판정·내성 및 일반적인 표현의 실제 모델 검증과 지원 범위 확장
- 일반적인 RisuAI 카드·로어 호환과 스트리밍(모델 서술에는 제한된 텍스트 문맥만 전달)
- 기존 JSONL 가져오기 및 향후 상태·이벤트 의미 체계 변경을 위한 마이그레이션
- 완전한 TRPG 규칙 세트
- 완전한 RuleCheeseBench 실행기와 평가기

이 저장소의 작업을 Codex나 Claude Code에 인계한다면 `docs/AGENT_HANDOFF.md`부터 확인하세요.

## 보안 및 배포 참고 사항

로컬 자격 증명은 `.env`에, 저장한 캠페인은 `data/` 아래에 보관하세요. Git과 Docker 빌드의 제외 규칙은
로컬 환경 파일, 개인 키, 데이터베이스와 부속 파일, 저널, 백업, 로컬 캐시를 포함합니다.
`.env.example`은 공개용 예시 값만 담고 있으므로 제외하지 않습니다.
설정은 실행 시 전달하며, 이미지에 포함하지 않습니다.

디버그 HTTP 서버에는 아직 운영 환경용 인증이나 테넌트 간 격리가 없습니다.
외부 API·인증 계층을 구현하고 검토하기 전까지는 localhost 또는 사설 네트워크에만 바인딩하세요.

명령어 데모 번들은 `packs/risu-starter/`에 있습니다. v2 카드와 v3 프로바이더는 버전이 고정된
PocketRisu 1.13.0 호스트를 대상으로 합니다. 지원 명령, 수정·재시도 동작, 처리 용량의 제약은
시작 안내를 참고하세요.

설치 방법, 지원 기능, 제약 사항을 변경할 때는 `README.md`와 `README.ko.md`를 같은 변경에서 함께 갱신하세요.
