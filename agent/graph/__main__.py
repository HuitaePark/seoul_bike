# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: agent 서버 진입점 (python -m graph). Kafka ai.tasks를 소비해 근무조 그래프를 돌린다.

import asyncio

from .wiring import main

asyncio.run(main())
