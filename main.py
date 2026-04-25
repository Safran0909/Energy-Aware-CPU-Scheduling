from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
import copy
import time
import psutil
import heapq

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# MODELS
# =========================================================

class ProcessInput(BaseModel):
    pid: int
    arrival: int
    burst: int


class Process:
    def __init__(self, pid, arrival, burst, name="Unknown"):
        self.pid = pid
        self.name = name
        self.arrival = arrival
        self.burst = burst
        self.remaining = burst
        self.waiting = 0
        self.turnaround = 0
        self.completed = False


# =========================================================
# LIVE PROCESS CAPTURE
# =========================================================

def get_live_processes(limit=10):

    ignore = [
        "System Idle Process",
        "System",
        "Registry",
        "smss.exe",
        "svchost.exe",
        "csrss.exe",
        "WUDFHost.exe",
        "winlogon.exe",
        "wininit.exe",
        "services.exe",
        "lsass.exe"
    ]

    processes = []

    psutil.cpu_percent(interval=0.1)

    for p in psutil.process_iter(
        ['pid', 'name', 'create_time', 'memory_info']
    ):
        try:
            name = p.info['name'] or "Unknown"
            lname = name.lower()

            if name in ignore:
                continue

            cpu = p.cpu_percent(interval=None)
            memory_mb = p.info['memory_info'].rss / (1024 * 1024)

            score = (cpu * 5) + (memory_mb / 800)

            if "touchdesigner" in lname:
                score += 8
            elif "msedge" in lname:
                score += 5
            elif "chrome" in lname:
                score += 5
            elif "code" in lname:
                score += 4
            elif "explorer" in lname:
                score += 2

            if score <= 0.5:
                continue

            now = time.time()
            age_seconds = max(0, now - p.info['create_time'])

            arrival = min(20, int(age_seconds / 15))
            burst = max(4, min(20, int(score) + 3))

            processes.append({
                "pid": p.info['pid'],
                "name": name,
                "arrival": arrival,
                "burst": burst,
                "score": score
            })

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess
        ):
            continue

    processes = sorted(
        processes,
        key=lambda x: x["score"],
        reverse=True
    )

    for idx, proc in enumerate(processes):
        proc["arrival"] = idx * 2

    if not processes:
        processes = [
            {
                "pid": 1,
                "name": "fallback1",
                "arrival": 0,
                "burst": 10,
                "score": 10
            },
            {
                "pid": 2,
                "name": "fallback2",
                "arrival": 2,
                "burst": 6,
                "score": 6
            }
        ]

    final = []

    for p in processes[:limit]:
        final.append({
            "pid": p["pid"],
            "name": p["name"],
            "arrival": p["arrival"],
            "burst": p["burst"]
        })

    return final


# =========================================================
# ENERGY MODEL
# =========================================================

def calc_energy(freq, runtime, core="Big", temp=40):
    static_power = 7.5 if core == "Big" else 2.8
    dynamic = (freq ** 2.15) * runtime
    leakage = (temp / 85.0) * runtime * (
        1.4 if core == "Big" else 0.7
    )
    return static_power + dynamic + leakage


# =========================================================
# FCFS
# =========================================================

def fcfs(processes):

    current_time = 0
    total_energy = 0
    big_temp = 40.0

    for p in sorted(processes, key=lambda x: x.arrival):

        if current_time < p.arrival:
            current_time = p.arrival

        p.waiting = current_time - p.arrival
        current_time += p.burst
        p.turnaround = p.waiting + p.burst

        total_energy += calc_energy(
            2.0,
            p.burst,
            "Big",
            big_temp
        )

        big_temp = min(88, big_temp + p.burst * 0.55)

    return processes, round(total_energy, 1)


# =========================================================
# SJF
# =========================================================

def sjf(processes):

    current_time = 0
    done = 0
    total_energy = 0
    big_temp = 38.0

    while done < len(processes):

        ready = [
            p for p in processes
            if p.arrival <= current_time and not p.completed
        ]

        if not ready:
            current_time += 1
            continue

        p = min(ready, key=lambda x: x.burst)

        p.waiting = current_time - p.arrival
        current_time += p.burst
        p.turnaround = p.waiting + p.burst
        p.completed = True
        done += 1

        if p.burst <= 6:
            freq = 1.4
        elif p.burst <= 12:
            freq = 1.7
        else:
            freq = 2.0

        total_energy += calc_energy(
            freq,
            p.burst,
            "Big",
            big_temp
        )

        big_temp = max(
            36,
            min(84, big_temp + p.burst * 0.35)
        )

    return processes, round(total_energy, 1)


# =========================================================
# AETAS
# =========================================================

def aetas(processes):

    current_time = 0
    done = 0
    n = len(processes)

    energy_total = 0
    thermal = []
    logs = []
    step_logs = []

    usage = {"Big": 0, "Little": 0}

    big_temp = 34.0
    little_temp = 28.0

    processes.sort(key=lambda x: x.arrival)

    i = 0
    ready = []

    while done < n:

        while i < n and processes[i].arrival <= current_time:
            p = processes[i]
            heapq.heappush(
                ready,
                (p.remaining, p.pid, p)
            )
            i += 1

        if not ready:
            current_time += 1
            big_temp = max(32, big_temp - 0.6)
            little_temp = max(27, little_temp - 0.3)
            continue

        _, _, p = heapq.heappop(ready)

        wait = current_time - p.arrival
        score = max(1, p.remaining - wait * 0.18)

        if score <= 5:
            core = "Little"
            freq = 1.0

        elif score <= 10:
            if big_temp > 78:
                core = "Little"
                freq = 1.2
            else:
                core = "Big"
                freq = 1.5

        else:
            if big_temp > 80:
                core = "Little"
                freq = 1.3
            else:
                core = "Big"
                freq = 1.8

        if core == "Little":
            quantum = min(2, p.remaining)
        else:
            quantum = min(4, p.remaining)

        start_time = current_time

        p.remaining -= quantum
        current_time += quantum

        if core == "Big":

            energy_total += calc_energy(
                freq,
                quantum,
                "Big",
                big_temp
            )

            big_temp += (freq * quantum * 1.9) - 1.4
            big_temp = min(big_temp, 82.5)

            usage["Big"] += quantum
            little_temp = max(27, little_temp - 0.25)

        else:

            energy_total += calc_energy(
                freq,
                quantum,
                "Little",
                little_temp
            )

            little_temp += (freq * quantum * 0.8) - 0.45
            little_temp = min(little_temp, 42)

            usage["Little"] += quantum
            big_temp = max(32, big_temp - 0.65)

        thermal.append({
            "time": current_time,
            "big": round(big_temp, 1),
            "little": round(little_temp, 1)
        })

        step_logs.append({
            "time": start_time,
            "selected": f"P_{p.pid}",
            "core": core,
            "freq": round(freq, 1),
            "core_reason":
                "Thermal balancing → Little Core"
                if core == "Little" and score > 5
                else f"Adaptive scheduling → {core} Core",
            "thermal": {
                "big": round(big_temp, 1),
                "little": round(little_temp, 1),
                "status":
                    "THROTTLED"
                    if big_temp > 80
                    else "OK"
            }
        })

        if p.remaining <= 0:

            p.completed = True
            done += 1

            p.turnaround = current_time - p.arrival
            p.waiting = p.turnaround - p.burst

            logs.append({
                "pid": f"P_{p.pid}",
                "name": p.name,
                "core": core,
                "freq": f"{round(freq,1)} GHz"
            })

        else:

            heapq.heappush(
                ready,
                (p.remaining, p.pid, p)
            )

    return (
        processes,
        round(energy_total, 1),
        thermal,
        usage,
        logs,
        step_logs
    )


# =========================================================
# MANUAL SIMULATION
# =========================================================

@app.post("/simulate")
def simulate(data: List[ProcessInput]):

    for p in data:
        if p.arrival < 0 or p.burst <= 0:
            raise ValueError("Invalid input")

    processes = [
        Process(p.pid, p.arrival, p.burst)
        for p in data
    ]

    fcfs_res, fcfs_e = fcfs(copy.deepcopy(processes))
    sjf_res, sjf_e = sjf(copy.deepcopy(processes))
    aetas_res, aetas_e, thermal, usage, logs, step_logs = aetas(
        copy.deepcopy(processes)
    )

    return build_response(
        fcfs_res,
        sjf_res,
        aetas_res,
        fcfs_e,
        sjf_e,
        aetas_e,
        thermal,
        usage,
        logs,
        step_logs
    )


# =========================================================
# LIVE SIMULATION
# =========================================================

@app.get("/simulate-live")
def simulate_live():

    raw = get_live_processes()

    processes = [
        Process(
            p["pid"],
            p["arrival"],
            p["burst"],
            p["name"]
        )
        for p in raw
    ]

    fcfs_res, fcfs_e = fcfs(copy.deepcopy(processes))
    sjf_res, sjf_e = sjf(copy.deepcopy(processes))
    aetas_res, aetas_e, thermal, usage, logs, step_logs = aetas(
        copy.deepcopy(processes)
    )

    return build_response(
        fcfs_res,
        sjf_res,
        aetas_res,
        fcfs_e,
        sjf_e,
        aetas_e,
        thermal,
        usage,
        logs,
        step_logs
    )


# =========================================================
# RESPONSE BUILDER
# =========================================================

def build_response(
    fcfs_res,
    sjf_res,
    aetas_res,
    fcfs_e,
    sjf_e,
    aetas_e,
    thermal,
    usage,
    logs,
    step_logs
):

    fcfs_wait = sum(
        p.waiting for p in fcfs_res
    ) / len(fcfs_res)

    sjf_wait = sum(
        p.waiting for p in sjf_res
    ) / len(sjf_res)

    aetas_wait = sum(
        p.waiting for p in aetas_res
    ) / len(aetas_res)

    fcfs_turn = sum(
        p.turnaround for p in fcfs_res
    ) / len(fcfs_res)

    sjf_turn = sum(
        p.turnaround for p in sjf_res
    ) / len(sjf_res)

    aetas_turn = sum(
        p.turnaround for p in aetas_res
    ) / len(aetas_res)

    return {
        "energy": aetas_e,
        "fcfs_energy": fcfs_e,
        "sjf_energy": sjf_e,

        "waiting": {
            "fcfs": round(fcfs_wait, 1),
            "sjf": round(sjf_wait, 1),
            "aetas": round(aetas_wait, 1)
        },

        "turnaround": {
            "fcfs": round(fcfs_turn, 1),
            "sjf": round(sjf_turn, 1),
            "aetas": round(aetas_turn, 1)
        },

        "efficiency": usage,
        "thermal": thermal,
        "logs": logs,
        "step_logs": step_logs
    }


# =========================================================
# RUN
# =========================================================
# uvicorn filename:app --reload
