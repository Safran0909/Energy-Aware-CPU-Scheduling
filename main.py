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

import psutil
import time

def get_live_processes(limit=10):
    import psutil
    import time

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

    # warm-up CPU readings
    psutil.cpu_percent(interval=0.1)

    for p in psutil.process_iter(['pid', 'name', 'create_time', 'memory_info']):
        try:
            name = p.info['name'] or "Unknown"
            lname = name.lower()

            # ignore system junk
            if name in ignore:
                continue

            cpu = p.cpu_percent(interval=None)
            memory_mb = p.info['memory_info'].rss / (1024 * 1024)

            # 🔥 Better live score
            score = (cpu * 5) + (memory_mb / 800)

            # preferred app boosts
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

            # skip useless tiny processes
            if score <= 0.5:
                continue

            arrival = len(processes) % 5

            # convert score → burst
            burst = max(4, min(20, int(score) + 3))

            processes.append({
                "pid": p.info['pid'],
                "name": name,
                "arrival": arrival,
                "burst": burst,
                "score": score
            })

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # 🔥 SORT FIRST (important)
    processes = sorted(processes, key=lambda x: x["score"], reverse=True)

    # fallback
    if not processes:
        processes = [
            {"pid": 1, "name": "fallback1", "arrival": 0, "burst": 10, "score": 10},
            {"pid": 2, "name": "fallback2", "arrival": 1, "burst": 6, "score": 6},
        ]

    # remove score before return
    final = []
    for p in processes[:limit]:
        final.append({
            "pid": p["pid"],
            "name": p["name"],
            "arrival": p["arrival"],
            "burst": p["burst"]
        })

    return final
@app.get("/simulate-live")
def simulate_live():

    raw = get_live_processes()
    
    processes = [
    Process(p["pid"], p["arrival"], p["burst"], p["name"])
    for p in raw
]
    

    fcfs_res, fcfs_e = fcfs(copy.deepcopy(processes))
    sjf_res, sjf_e = sjf(copy.deepcopy(processes))
    aetas_res, aetas_e, thermal, usage, logs, step_logs = aetas(copy.deepcopy(processes))

    # ✅ calculate metrics FIRST
    fcfs_wait = sum(p.waiting for p in fcfs_res) / len(fcfs_res)
    sjf_wait = sum(p.waiting for p in sjf_res) / len(sjf_res)
    aetas_wait = sum(p.waiting for p in aetas_res) / len(aetas_res)

    fcfs_turn = sum(p.turnaround for p in fcfs_res) / len(fcfs_res)
    sjf_turn = sum(p.turnaround for p in sjf_res) / len(sjf_res)
    aetas_turn = sum(p.turnaround for p in aetas_res) / len(aetas_res)

    # ✅ THEN return
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
        "logs": logs,
        "step_logs": step_logs,
        "source": "LIVE_SYSTEM"
    }
# ---------- MODEL ----------
class ProcessInput(BaseModel):
    pid: int
    arrival: int
    burst: int

# ---------- LOGIC ----------
class Process:
    def __init__(self, pid, arrival, burst, name="Unknown"):
        self.pid = pid
        self.name = name   # 🔥 ADD THIS
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
    import time
    start_time = time.time()

    current_time,done = 0, 0
    energy_total = 0

    thermal = []
    usage = {"Big": 0, "Little": 0}

    logs = []        # existing dashboard logs
    step_logs = []   # NEW step-by-step logs

    big_temp, little_temp = 30, 30

    # initialize predicted burst
    for p in processes:
        p.predicted = p.burst

    max_time = 100 # reduce

    while done < len(processes) and current_time < max_time:

        ready = [p for p in processes if p.arrival <= current_time and not p.completed]
        if time.time() - start_time > 2:
            print("⚠️ FORCE BREAK (too slow)")
            break

        if not ready:
            current_time += 1
            continue

        # safety: break if stuck
        if current_time> 300:
            print("⚠️ Breaking early (possible bad input)")
            break
        # cooling
        big_temp = max(25, big_temp - 1)
        little_temp = max(25, little_temp - 1)

        ready = [p for p in processes if p.arrival <= current_time and not p.completed]

        if not ready:
            current_time += 1
            continue

        # -------- SELECTION --------
        heap = []

        for p in ready:
            p.predicted = p.burst

            waiting = current_time - p.arrival
            effective_wait = waiting + (current_time* 0.05)

            heapq.heappush(heap, (p.predicted + effective_wait, p.pid, p))

        _, _, p = heapq.heappop(heap)

        # -------- CORE SELECTION --------
        if p.predicted >= 7:
            core = "Big"
            freq = 2.0
            reason = "High load → Big Core"

        elif p.predicted >= 5:
            core = "Big"
            freq = 1.8
            reason = "Medium load → Big Core (low freq)"

        else:
            core = "Little"
            freq = 1.2
            reason = "Low load → Little Core"

        # thermal throttle after decision
        if core == "Big" and big_temp >= 85:
            core = "Little"
            freq = 1.0
            reason = "Thermal throttle → Little Core"

        # update temps + usage
        if core == "Big":
            big_temp += 2
            usage["Big"] += 1
            temp = big_temp
        else:
            little_temp += 1
            usage["Little"] += 1
            temp = little_temp

        # 🔥 -------- THERMAL LOGGING (was missing!) --------
        thermal.append({
            "time": current_time,
            "big": round(big_temp, 1),
            "little": round(little_temp, 1)
        })

        # 🔥 -------- STEP LOGGING --------
        ready_ids = [f"P_{x.pid}" for x in ready]

        predicted_map = {
            f"P_{x.pid}": round(x.predicted, 2) for x in ready
        }

        score_map = {}
        for x in ready:
            waiting = current_time - x.arrival
            effective_wait = waiting + (current_time * 0.05)
            score_map[f"P_{x.pid}"] = round(x.predicted + effective_wait, 2)

        step_logs.append({
            "time": current_time,
            "ready_queue": ready_ids,
            "predicted_map": predicted_map,
            "score_map": score_map,
            "selected": f"P_{p.pid}",

            "core": core,
            "freq": freq,

            "core_reason": reason,

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
            p.turnaround =current_time - p.arrival + 1
            p.waiting = p.turnaround - p.burst

            logs.append({
                "pid": f"P_{p.pid}",
                "name": p.name,
                "core": core,
                "freq": f"{freq} GHz"
            })

        current_time += 1

    return processes, energy_total, thermal, usage, logs, step_logs


# ---------- API ----------
@app.post("/simulate")
def simulate(data: List[ProcessInput]):
    for p in data:
        if p.arrival < 0 or p.burst <= 0:
            raise ValueError("Invalid input detected")
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
