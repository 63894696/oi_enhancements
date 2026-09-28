#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bench_perf_local.py — M3.51 本地 perf_conf 评测 harness(2026-09-23)
#
# 复用 bench_intents_local.py 模式,50 条 TEST_CASES (5 类各 10)
# 输出 accuracy / per-class / confusion / latency p50 / parse_fail
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# 5 类各 10 条样本(对齐 bench_intents 模式)
TEST_CASES = [
    # ---- safe (10) ----
    {"risk": "safe", "sample": {
        "ts": "2026-09-23T10:00:00Z",
        "cpu": {"pct": 5, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 25, "used_gb": 8, "total_gb": 32, "available_gb": 24},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "VMware VMnet1", "isup": True}]},
        "system": {"uptime_s": 3000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "safe", "sample": {
        "ts": "2026-09-23T11:00:00Z",
        "cpu": {"pct": 30, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 30, "used_gb": 10, "total_gb": 32, "available_gb": 22},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "WireGuard", "isup": True}]},
        "system": {"uptime_s": 3200, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "safe", "sample": {
        "ts": "2026-09-23T12:00:00Z",
        "cpu": {"pct": 20, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 35, "used_gb": 11, "total_gb": 32, "available_gb": 21},
        "net": {"n_total": 9, "n_up": 6, "nics": []},
        "system": {"uptime_s": 3500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "safe", "sample": {
        "ts": "2026-09-23T13:00:00Z",
        "cpu": {"pct": 45, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 50, "used_gb": 16, "total_gb": 32, "available_gb": 16},
        "net": {"n_total": 9, "n_up": 6, "nics": []},
        "system": {"uptime_s": 4000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "safe", "sample": {
        "ts": "2026-09-23T14:00:00Z",
        "cpu": {"pct": 35, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 45, "used_gb": 14, "total_gb": 32, "available_gb": 18},
        "net": {"n_total": 9, "n_up": 6, "nics": []},
        "system": {"uptime_s": 4500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "safe", "sample": {
        "ts": "2026-09-23T15:00:00Z",
        "cpu": {"pct": 25, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 40, "used_gb": 13, "total_gb": 32, "available_gb": 19},
        "net": {"n_total": 9, "n_up": 6, "nics": []},
        "system": {"uptime_s": 5000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "safe", "sample": {
        "ts": "2026-09-23T16:00:00Z",
        "cpu": {"pct": 15, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 30, "used_gb": 10, "total_gb": 32, "available_gb": 22},
        "net": {"n_total": 9, "n_up": 6, "nics": []},
        "system": {"uptime_s": 5500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "safe", "sample": {
        "ts": "2026-09-23T17:00:00Z",
        "cpu": {"pct": 10, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 35, "used_gb": 11, "total_gb": 32, "available_gb": 21},
        "net": {"n_total": 9, "n_up": 6, "nics": []},
        "system": {"uptime_s": 6000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "safe", "sample": {
        "ts": "2026-09-23T18:00:00Z",
        "cpu": {"pct": 20, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 32, "used_gb": 10, "total_gb": 32, "available_gb": 22},
        "net": {"n_total": 9, "n_up": 6, "nics": []},
        "system": {"uptime_s": 6500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "safe", "sample": {
        "ts": "2026-09-23T19:00:00Z",
        "cpu": {"pct": 30, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 38, "used_gb": 12, "total_gb": 32, "available_gb": 20},
        "net": {"n_total": 9, "n_up": 6, "nics": []},
        "system": {"uptime_s": 7000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},

    # ---- low (10) ----
    {"risk": "low", "sample": {
        "ts": "2026-09-23T20:00:00Z",
        "cpu": {"pct": 65, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 60, "used_gb": 19, "total_gb": 32, "available_gb": 13},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "以太网 2", "isup": False}]},
        "system": {"uptime_s": 7500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "low", "sample": {
        "ts": "2026-09-23T20:30:00Z",
        "cpu": {"pct": 70, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 55, "used_gb": 18, "total_gb": 32, "available_gb": 14},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}]},
        "system": {"uptime_s": 7800, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "low", "sample": {
        "ts": "2026-09-23T21:00:00Z",
        "cpu": {"pct": 50, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 65, "used_gb": 21, "total_gb": 32, "available_gb": 11},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "vktap", "isup": False}]},
        "system": {"uptime_s": 8000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "low", "sample": {
        "ts": "2026-09-23T21:30:00Z",
        "cpu": {"pct": 55, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 50, "used_gb": 16, "total_gb": 32, "available_gb": 16},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 8200, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "low", "sample": {
        "ts": "2026-09-23T22:00:00Z",
        "cpu": {"pct": 60, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 58, "used_gb": 18, "total_gb": 32, "available_gb": 14},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}]},
        "system": {"uptime_s": 8500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "low", "sample": {
        "ts": "2026-09-23T22:30:00Z",
        "cpu": {"pct": 65, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 62, "used_gb": 20, "total_gb": 32, "available_gb": 12},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "Ethernet", "isup": False}]},
        "system": {"uptime_s": 8800, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "low", "sample": {
        "ts": "2026-09-23T23:00:00Z",
        "cpu": {"pct": 70, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 50, "used_gb": 16, "total_gb": 32, "available_gb": 16},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "vktap", "isup": False}]},
        "system": {"uptime_s": 9000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "low", "sample": {
        "ts": "2026-09-24T00:30:00Z",
        "cpu": {"pct": 55, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 60, "used_gb": 19, "total_gb": 32, "available_gb": 13},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap", "isup": False}]},
        "system": {"uptime_s": 9500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "low", "sample": {
        "ts": "2026-09-24T01:00:00Z",
        "cpu": {"pct": 68, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 55, "used_gb": 18, "total_gb": 32, "available_gb": 14},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 10000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "low", "sample": {
        "ts": "2026-09-24T01:30:00Z",
        "cpu": {"pct": 60, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 52, "used_gb": 17, "total_gb": 32, "available_gb": 15},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}]},
        "system": {"uptime_s": 10500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},

    # ---- medium (10) ----
    {"risk": "medium", "sample": {
        "ts": "2026-09-24T02:00:00Z",
        "cpu": {"pct": 85, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 80, "used_gb": 26, "total_gb": 32, "available_gb": 6},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}]},
        "system": {"uptime_s": 11000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "medium", "sample": {
        "ts": "2026-09-24T02:30:00Z",
        "cpu": {"pct": 80, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 85, "used_gb": 27, "total_gb": 32, "available_gb": 5},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap", "isup": False}, {"nic": "tun", "isup": False}]},
        "system": {"uptime_s": 11500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "medium", "sample": {
        "ts": "2026-09-24T03:00:00Z",
        "cpu": {"pct": 90, "count": 6, "freq_mhz": 3200},
        "memory": {"used_pct": 70, "used_gb": 22, "total_gb": 32, "available_gb": 10},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}]},
        "system": {"uptime_s": 12000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 1},
    }},
    {"risk": "medium", "sample": {
        "ts": "2026-09-24T03:30:00Z",
        "cpu": {"pct": 82, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 75, "used_gb": 24, "total_gb": 32, "available_gb": 8},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 12500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "medium", "sample": {
        "ts": "2026-09-24T04:00:00Z",
        "cpu": {"pct": 78, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 82, "used_gb": 26, "total_gb": 32, "available_gb": 6},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}]},
        "system": {"uptime_s": 13000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "medium", "sample": {
        "ts": "2026-09-24T04:30:00Z",
        "cpu": {"pct": 88, "count": 6, "freq_mhz": 3500},
        "memory": {"used_pct": 60, "used_gb": 19, "total_gb": 32, "available_gb": 13},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap", "isup": False}, {"nic": "tun", "isup": False}]},
        "system": {"uptime_s": 13500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "medium", "sample": {
        "ts": "2026-09-24T05:00:00Z",
        "cpu": {"pct": 75, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 78, "used_gb": 25, "total_gb": 32, "available_gb": 7},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}]},
        "system": {"uptime_s": 14000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "medium", "sample": {
        "ts": "2026-09-24T05:30:00Z",
        "cpu": {"pct": 80, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 70, "used_gb": 22, "total_gb": 32, "available_gb": 10},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tapprotonvpn", "isup": False}, {"nic": "vktap", "isup": False}]},
        "system": {"uptime_s": 14500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "medium", "sample": {
        "ts": "2026-09-24T06:00:00Z",
        "cpu": {"pct": 85, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 80, "used_gb": 26, "total_gb": 32, "available_gb": 6},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 15000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},
    {"risk": "medium", "sample": {
        "ts": "2026-09-24T06:30:00Z",
        "cpu": {"pct": 78, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 72, "used_gb": 23, "total_gb": 32, "available_gb": 9},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}]},
        "system": {"uptime_s": 15500, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
    }},

    # ---- high (10) ----
    {"risk": "high", "sample": {
        "ts": "2026-09-24T07:00:00Z",
        "cpu": {"pct": 92, "count": 6, "freq_mhz": 3500},
        "memory": {"used_pct": 92, "used_gb": 29, "total_gb": 32, "available_gb": 3},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 16000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 1},
    }},
    {"risk": "high", "sample": {
        "ts": "2026-09-24T07:30:00Z",
        "cpu": {"pct": 88, "count": 6, "freq_mhz": 3200},
        "memory": {"used_pct": 95, "used_gb": 30, "total_gb": 32, "available_gb": 2},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tap", "isup": False}]},
        "system": {"uptime_s": 16500, "user": "Administrator"},
        "crash": {"bugcheck_count": 1, "kp41_count": 1},
    }},
    {"risk": "high", "sample": {
        "ts": "2026-09-24T08:00:00Z",
        "cpu": {"pct": 95, "count": 6, "freq_mhz": 3500},
        "memory": {"used_pct": 88, "used_gb": 28, "total_gb": 32, "available_gb": 4},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 17000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 3},
    }},
    {"risk": "high", "sample": {
        "ts": "2026-09-24T08:30:00Z",
        "cpu": {"pct": 91, "count": 6, "freq_mhz": 3200},
        "memory": {"used_pct": 90, "used_gb": 29, "total_gb": 32, "available_gb": 3},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tun", "isup": False}]},
        "system": {"uptime_s": 17500, "user": "Administrator"},
        "crash": {"bugcheck_count": 1, "kp41_count": 2},
    }},
    {"risk": "high", "sample": {
        "ts": "2026-09-24T09:00:00Z",
        "cpu": {"pct": 93, "count": 6, "freq_mhz": 3000},
        "memory": {"used_pct": 96, "used_gb": 31, "total_gb": 32, "available_gb": 1},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 18000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 1},
    }},
    {"risk": "high", "sample": {
        "ts": "2026-09-24T09:30:00Z",
        "cpu": {"pct": 89, "count": 6, "freq_mhz": 3500},
        "memory": {"used_pct": 93, "used_gb": 30, "total_gb": 32, "available_gb": 2},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tap", "isup": False}]},
        "system": {"uptime_s": 18500, "user": "Administrator"},
        "crash": {"bugcheck_count": 1, "kp41_count": 1},
    }},
    {"risk": "high", "sample": {
        "ts": "2026-09-24T10:00:00Z",
        "cpu": {"pct": 94, "count": 6, "freq_mhz": 3300},
        "memory": {"used_pct": 89, "used_gb": 28, "total_gb": 32, "available_gb": 4},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 19000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 2},
    }},
    {"risk": "high", "sample": {
        "ts": "2026-09-24T10:30:00Z",
        "cpu": {"pct": 90, "count": 6, "freq_mhz": 3200},
        "memory": {"used_pct": 94, "used_gb": 30, "total_gb": 32, "available_gb": 2},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tun", "isup": False}]},
        "system": {"uptime_s": 19500, "user": "Administrator"},
        "crash": {"bugcheck_count": 1, "kp41_count": 1},
    }},
    {"risk": "high", "sample": {
        "ts": "2026-09-24T11:00:00Z",
        "cpu": {"pct": 92, "count": 6, "freq_mhz": 3500},
        "memory": {"used_pct": 91, "used_gb": 29, "total_gb": 32, "available_gb": 3},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 20000, "user": "Administrator"},
        "crash": {"bugcheck_count": 0, "kp41_count": 1},
    }},
    {"risk": "high", "sample": {
        "ts": "2026-09-24T11:30:00Z",
        "cpu": {"pct": 95, "count": 6, "freq_mhz": 3000},
        "memory": {"used_pct": 88, "used_gb": 28, "total_gb": 32, "available_gb": 4},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tap", "isup": False}]},
        "system": {"uptime_s": 20500, "user": "Administrator"},
        "crash": {"bugcheck_count": 1, "kp41_count": 2},
    }},

    # ---- critical (10) — ndis BSOD fingerprint match ----
    {"risk": "critical", "sample": {
        "ts": "2026-09-23T03:26:05Z",
        "cpu": {"pct": 5, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 15, "used_gb": 5, "total_gb": 32, "available_gb": 27},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 18, "user": "Administrator"},
        "crash": {"bugcheck_count": 5, "kp41_count": 5},
    }},
    {"risk": "critical", "sample": {
        "ts": "2026-09-23T03:00:00Z",
        "cpu": {"pct": 3, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 20, "used_gb": 6, "total_gb": 32, "available_gb": 26},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 30, "user": "Administrator"},
        "crash": {"bugcheck_count": 4, "kp41_count": 4},
    }},
    {"risk": "critical", "sample": {
        "ts": "2026-09-22T23:32:00Z",
        "cpu": {"pct": 10, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 25, "used_gb": 8, "total_gb": 32, "available_gb": 24},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 22, "user": "Administrator"},
        "crash": {"bugcheck_count": 3, "kp41_count": 3},
    }},
    {"risk": "critical", "sample": {
        "ts": "2026-09-20T20:43:00Z",
        "cpu": {"pct": 8, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 30, "used_gb": 10, "total_gb": 32, "available_gb": 22},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 25, "user": "Administrator"},
        "crash": {"bugcheck_count": 3, "kp41_count": 3},
    }},
    {"risk": "critical", "sample": {
        "ts": "2026-09-19T22:37:00Z",
        "cpu": {"pct": 4, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 28, "used_gb": 9, "total_gb": 32, "available_gb": 23},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 35, "user": "Administrator"},
        "crash": {"bugcheck_count": 3, "kp41_count": 3},
    }},
    {"risk": "critical", "sample": {
        "ts": "2026-09-09T08:50:00Z",
        "cpu": {"pct": 12, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 22, "used_gb": 7, "total_gb": 32, "available_gb": 25},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 28, "user": "Administrator"},
        "crash": {"bugcheck_count": 2, "kp41_count": 2},
    }},
    {"risk": "critical", "sample": {
        "ts": "2026-09-23T04:00:00Z",
        "cpu": {"pct": 100, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 35, "used_gb": 11, "total_gb": 32, "available_gb": 21},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 2000, "user": "Administrator"},
        "crash": {"bugcheck_count": 5, "kp41_count": 5},
    }},
    {"risk": "critical", "sample": {
        "ts": "2026-09-22T02:00:00Z",
        "cpu": {"pct": 100, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 40, "used_gb": 13, "total_gb": 32, "available_gb": 19},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 1500, "user": "Administrator"},
        "crash": {"bugcheck_count": 3, "kp41_count": 3},
    }},
    {"risk": "critical", "sample": {
        "ts": "2026-09-21T03:30:00Z",
        "cpu": {"pct": 99, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 45, "used_gb": 14, "total_gb": 32, "available_gb": 18},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 800, "user": "Administrator"},
        "crash": {"bugcheck_count": 3, "kp41_count": 3},
    }},
    {"risk": "critical", "sample": {
        "ts": "2026-09-15T03:26:00Z",
        "cpu": {"pct": 7, "count": 6, "freq_mhz": 3696},
        "memory": {"used_pct": 50, "used_gb": 16, "total_gb": 32, "available_gb": 16},
        "net": {"n_total": 9, "n_up": 6, "nics": [{"nic": "tap0901", "isup": False}, {"nic": "vktap", "isup": False}, {"nic": "tapprotonvpn", "isup": False}]},
        "system": {"uptime_s": 15, "user": "Administrator"},
        "crash": {"bugcheck_count": 2, "kp41_count": 2},
    }},
]


def run_bench(adapter, cases=None):
    """Run perf bench. Returns dict of metrics."""
    cases = cases if cases is not None else TEST_CASES
    from classify_perf import classify_perf
    n = len(cases)
    correct = 0
    parse_fail = 0
    per_class_correct = {}
    per_class_total = {}
    confusion = {}  # (true, pred) -> count
    latencies = []
    confusion_records = []
    for tc in cases:
        truth = tc["risk"]
        per_class_total[truth] = per_class_total.get(truth, 0) + 1
        out = classify_perf(adapter, tc["sample"])
        latencies.append(out["latency_ms"])
        pred = out["risk"]
        if out["parse_fail"]:
            parse_fail += 1
        if pred == truth:
            correct += 1
            per_class_correct[truth] = per_class_correct.get(truth, 0) + 1
        confusion[(truth, pred)] = confusion.get((truth, pred), 0) + 1
        confusion_records.append({"truth": truth, "pred": pred,
                                  "raw": out["raw"][:60], "latency_ms": out["latency_ms"]})
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    return {
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4),
        "parse_fail": parse_fail,
        "parse_fail_rate": round(parse_fail / n, 4),
        "per_class": {k: {"correct": per_class_correct.get(k, 0), "total": v,
                          "accuracy": round(per_class_correct.get(k, 0) / v, 4) if v else 0}
                       for k, v in per_class_total.items()},
        "confusion": {f"{t}->{p}": c for (t, p), c in confusion.items() if t != p},
        "latency_ms": {"p50": p50, "p95": p95, "min": latencies[0], "max": latencies[-1]},
        "records": confusion_records,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="reports/bench_perf_local.json")
    # M3.62 加:--adapter 参数选 perf_conf / perf_conf_v2 / perf_conf_v3
    ap.add_argument("--adapter", default="perf_conf",
                    help="adapter 名(perf_conf/perf_conf_v2/perf_conf_v3)")
    args = ap.parse_args()

    from adapter_registry import get_adapter
    adapter = get_adapter(args.adapter)
    print(f"[bench_perf] adapter: {adapter.spec.name}", file=sys.stderr)
    report = run_bench(adapter, TEST_CASES)
    print(f"[bench_perf] accuracy={report['accuracy']} parse_fail={report['parse_fail_rate']} "
          f"p50={report['latency_ms']['p50']}ms", file=sys.stderr)
    out_path = _HERE / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bench_perf] written: {out_path}")
    # per-class print
    for cls, m in report["per_class"].items():
        print(f"  {cls:8s}: {m['correct']}/{m['total']} ({m['accuracy']*100:.0f}%)", file=sys.stderr)


if __name__ == "__main__":
    main()