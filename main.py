from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
import copy

app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- MODEL ----------
class ProcessInput(BaseModel):
    pid: int
    arrival: int
    burst: int

# ---------- LOGIC ----------
class Process:
    def __init__(self, pid, arrival, burst):
        self.pid = pid
        self.arrival = arrival
        self.burst = burst
        self.remaining = burst
        self.turnaround = 0
        self.waiting = 0
        self.completed = False

def energy(freq, t):
    return (freq**2) * t

def fcfs(processes):
    time, total_energy = 0, 0
    for p in sorted(processes, key=lambda x: x.arrival):
        if time < p.arrival:
            time = p.arrival
        p.waiting = time - p.arrival
        time += p.burst
        p.turnaround = p.waiting + p.burst
        total_energy += energy(2, p.burst)
    return processes, total_energy

def sjf(processes):
    time, done, energy_total = 0, 0, 0
    while done < len(processes):
        ready = [p for p in processes if p.arrival <= time and not p.completed]
        if not ready:
            time += 1
            continue
        p = min(ready, key=lambda x: x.burst)
        p.waiting = time - p.arrival
        time += p.burst
        p.turnaround = p.waiting + p.burst
        p.completed = True
        done += 1

        freq = 1.8 if p.burst < 10 else 2.0
        energy_total += energy(freq, p.burst)

    return processes, energy_total


# 🔥 -------- IMPROVED AETAS --------
def aetas(processes):
    import heapq

    time, done = 0, 0
    energy_total = 0

    thermal = []
    usage = {"Big": 0, "Little": 0}

    logs = []        # existing dashboard logs
    step_logs = []   # NEW step-by-step logs

    big_temp, little_temp = 30, 30

    # initialize predicted burst
    for p in processes:
        p.predicted = p.burst

    while done < len(processes):

        # thermal logging
        thermal.append({
            "time": time,
            "big": big_temp,
            "little": little_temp
        })

        # cooling
        big_temp = max(25, big_temp - 1)
        little_temp = max(25, little_temp - 1)

        ready = [p for p in processes if p.arrival <= time and not p.completed]

        if not ready:
            time += 1
            continue

        # -------- SELECTION --------
        heap = []

        for p in ready:
            p.predicted = 0.5 * p.burst + 0.5 * p.predicted

            waiting = time - p.arrival
            effective_wait = waiting + (time * 0.05)

            heapq.heappush(heap, (p.predicted + effective_wait, p))

        _, p = heapq.heappop(heap)

        # -------- CORE SELECTION --------
        if p.predicted > 15:
            core = "Big"
            freq = 2.0
            big_temp += 2
            usage["Big"] += 1
            temp = big_temp

        elif p.predicted > 8:
            core = "Big"
            freq = 1.8
            big_temp += 1.5
            usage["Big"] += 1
            temp = big_temp

        else:
            core = "Little"
            freq = 1.2
            little_temp += 1
            usage["Little"] += 1
            temp = little_temp

        # -------- THERMAL SWITCH --------
        if temp > 85:
            core = "Little"
            freq = 1.0
            little_temp += 0.5
            usage["Little"] += 1
            temp = little_temp

        # 🔥 -------- STEP LOGGING --------
        ready_ids = [f"P_{x.pid}" for x in ready]

        predicted_map = {
            f"P_{x.pid}": round(x.predicted, 2) for x in ready
        }

        score_map = {}
        for x in ready:
            waiting = time - x.arrival
            effective_wait = waiting + (time * 0.05)
            score_map[f"P_{x.pid}"] = round(x.predicted + effective_wait, 2)

        step_logs.append({
            "time": time,
            "ready_queue": ready_ids,
            "predicted_map": predicted_map,
            "score_map": score_map,
            "selected": f"P_{p.pid}",

            "core": core,
            "freq": freq,

            "core_reason": (
                "High load → Big Core" if p.predicted > 15 else
                "Medium load → Big Core (low freq)" if p.predicted > 8 else
                "Low load → Little Core"
            ),

            "thermal": {
                "big": round(big_temp, 1),
                "little": round(little_temp, 1),
                "status": "THROTTLED" if temp > 85 else "OK"
            }
        })

        # -------- EXECUTION --------
        energy_total += energy(freq, 1)
        p.remaining -= 1

        # -------- COMPLETION --------
        if p.remaining <= 0:
            p.completed = True
            done += 1
            p.turnaround = time - p.arrival + 1
            p.waiting = p.turnaround - p.burst

            logs.append({
                "pid": f"P_{p.pid}",
                "core": core,
                "freq": f"{freq} GHz"
            })

        time += 1

    return processes, energy_total, thermal, usage, logs, step_logs


# ---------- API ----------
@app.post("/simulate")
def simulate(data: List[ProcessInput]):
    processes = [Process(p.pid, p.arrival, p.burst) for p in data]

    fcfs_res, fcfs_e = fcfs(copy.deepcopy(processes))
    sjf_res, sjf_e = sjf(copy.deepcopy(processes))

    aetas_res, aetas_e, thermal, usage, logs, step_logs = aetas(copy.deepcopy(processes))

    fcfs_wait = sum(p.waiting for p in fcfs_res) / len(fcfs_res)
    sjf_wait = sum(p.waiting for p in sjf_res) / len(sjf_res)
    aetas_wait = sum(p.waiting for p in aetas_res) / len(aetas_res)

    fcfs_turn = sum(p.turnaround for p in fcfs_res) / len(fcfs_res)
    sjf_turn = sum(p.turnaround for p in sjf_res) / len(sjf_res)
    aetas_turn = sum(p.turnaround for p in aetas_res) / len(aetas_res)

    return {
        "energy": aetas_e,
        "fcfs_energy": fcfs_e,
        "sjf_energy": sjf_e,

        "waiting": {
            "fcfs": fcfs_wait,
            "sjf": sjf_wait,
            "aetas": aetas_wait
        },

        "turnaround": {
            "fcfs": fcfs_turn,
            "sjf": sjf_turn,
            "aetas": aetas_turn
        },

        "efficiency": usage,
        "thermal": thermal,

        "logs": logs,            # old logs (dashboard safe)
        "step_logs": step_logs   # NEW step-by-step logs
    }