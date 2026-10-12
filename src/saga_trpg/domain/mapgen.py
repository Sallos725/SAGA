"""Seeded room-graph generator (ADR 0009).

Pure: the same seed material always gives the same map. A generated map is stored in the
world's initial state, so changing this module (and bumping GENERATOR_VERSION) never alters
an existing world; replay reads the stored map and does not call the generator. Python only
documents `random()` itself as reproducible across versions, so the stored map, not
regeneration, is the replay guarantee; the golden digest test catches drift between versions.
"""

from __future__ import annotations

import hashlib
import random

from saga_trpg.domain.models import Exit, MapGenerator, MapState, Room

GENERATOR_NAME = "saga-rooms"
GENERATOR_VERSION = "1"
MIN_ROOMS = 2
MAX_ROOMS = 30

OPPOSITE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
    "up": "down",
    "down": "up",
}
DIRECTION_WORDS = {
    "north": "북쪽",
    "south": "남쪽",
    "east": "동쪽",
    "west": "서쪽",
    "up": "위쪽",
    "down": "아래쪽",
}
KIND_WORDS = {"open": "통로", "door": "문"}

# Room name and description pairs; the first is the start room. At least MAX_ROOMS entries,
# so names never repeat within a map.
ROOMS = (
    ("입구 홀", "먼지 쌓인 돌바닥에 오래된 발자국이 흐릿하게 남아 있다."),
    ("경비실", "낡은 무기 거치대가 벽을 따라 늘어선 좁은 방."),
    ("창고", "썩은 나무 상자와 찢어진 자루가 어지럽게 쌓여 있다."),
    ("지하 예배당", "금이 간 제단 위로 촛농이 굳어 흘러내려 있다."),
    ("무너진 서재", "책장 절반이 쓰러져 종잇조각이 바닥을 덮고 있다."),
    ("우물방", "방 한가운데 둥근 우물에서 차가운 바람이 올라온다."),
    ("감옥", "녹슨 쇠창살 너머로 빈 족쇄가 매달려 있다."),
    ("부엌", "그을린 화덕과 깨진 냄비가 남아 있다."),
    ("식당", "긴 탁자 위에 먼지 덮인 식기가 그대로 놓여 있다."),
    ("병영", "삐걱거리는 이층 침대가 줄지어 서 있다."),
    ("무기고", "빈 선반마다 칼집 자국만 남아 있다."),
    ("보물고", "두꺼운 철문 안쪽 벽에 빈 진열대가 박혀 있다."),
    ("연금술 작업실", "깨진 유리 기구에서 시큼한 냄새가 난다."),
    ("지하수로", "발목까지 차오른 물이 천천히 흐른다."),
    ("버섯 동굴", "희미하게 빛나는 버섯이 천장까지 자라 있다."),
    ("석상 회랑", "얼굴이 깎여 나간 석상들이 양쪽에 서 있다."),
    ("납골당", "벽감마다 이름 모를 유골함이 놓여 있다."),
    ("훈련장", "모래 바닥에 부서진 허수아비가 쓰러져 있다."),
    ("마구간", "오래된 건초 냄새가 아직 남아 있다."),
    ("탑 계단참", "좁은 창으로 바깥 빛이 가늘게 들어온다."),
    ("기도실", "작은 방석 세 개가 빈 제단을 향해 놓여 있다."),
    ("포도주 저장고", "깨진 술통에서 흘러나온 얼룩이 바닥에 말라붙어 있다."),
    ("하인 숙소", "얇은 매트리스와 작은 궤짝이 몇 개 남아 있다."),
    ("지도실", "벽에 바랜 지도가 핀으로 고정되어 있다."),
    ("거미굴", "구석마다 두꺼운 거미줄이 늘어져 있다."),
    ("붕괴한 통로 끝방", "천장 일부가 내려앉아 돌무더기가 쌓여 있다."),
    ("의식의 방", "바닥에 분필로 그린 원이 반쯤 지워져 있다."),
    ("수비대장실", "부서진 책상 위에 찢어진 명령서가 흩어져 있다."),
    ("환기 갱도", "벽 틈새로 바람이 휘파람 소리를 낸다."),
    ("지하 호수", "검은 물이 고요하게 고여 끝이 보이지 않는다."),
)


def generate_map(seed_material: str, *, min_rooms: int, max_rooms: int) -> MapState:
    if not MIN_ROOMS <= min_rooms <= max_rooms <= MAX_ROOMS:
        raise ValueError(f"room count needs {MIN_ROOMS} <= min <= max <= {MAX_ROOMS}")
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest, "big"))
    count = rng.randint(min_rooms, max_rooms)
    # The start room is always the entrance hall; the rest are drawn without repetition.
    picks = [0, *rng.sample(range(1, len(ROOMS)), count - 1)]
    ids = [f"room_{index + 1}" for index in range(count)]
    links: dict[str, dict[str, tuple[str, str]]] = {room_id: {} for room_id in ids}

    def free(a: str, b: str) -> list[str]:
        return [d for d in OPPOSITE if d not in links[a] and OPPOSITE[d] not in links[b]]

    def connect(a: str, b: str, direction: str) -> None:
        kind = "door" if rng.randrange(3) == 0 else "open"
        links[a][direction] = (b, kind)
        links[b][OPPOSITE[direction]] = (a, kind)

    # Spanning tree: every room joins an earlier one, so all are reachable from room_1.
    # A tree on k rooms has k-1 links, so some earlier room always has a free direction.
    for index in range(1, count):
        new = ids[index]
        parent = rng.choice([old for old in ids[:index] if free(old, new)])
        connect(parent, new, rng.choice(free(parent, new)))
    # A few extra links make loops; an attempt is skipped if the rooms are already linked
    # or no direction pair is free in both.
    for _ in range(count // 4):
        a, b = rng.sample(ids, 2)
        options = free(a, b)
        if options and all(to != b for to, _ in links[a].values()):
            connect(a, b, rng.choice(options))

    rooms = {}
    for room_id, pick in zip(ids, picks, strict=True):
        name, description = ROOMS[pick]
        rooms[room_id] = Room(
            name=name,
            description=description,
            exits={
                direction: Exit(
                    to_room=to,
                    label=f"{DIRECTION_WORDS[direction]} {KIND_WORDS[kind]}",
                    kind=kind,
                )
                for direction, (to, kind) in sorted(links[room_id].items())
            },
        )
    return MapState(
        start_room=ids[0],
        rooms=rooms,
        generator=MapGenerator(
            name=GENERATOR_NAME,
            version=GENERATOR_VERSION,
            params={"min_rooms": min_rooms, "max_rooms": max_rooms},
        ),
    )
