#!/bin/bash
# Demo for social media. Narrated via on-screen text.
# Rendered with: vhs demo.tape

ORANGE='\033[38;2;212;118;78m'
DIM='\033[38;2;88;91;112m'
GREEN='\033[38;2;166;218;149m'
YELLOW='\033[38;2;249;226;175m'
RED='\033[38;2;243;139;168m'
WHITE='\033[38;2;205;214;244m'
B='\033[1m'
R='\033[0m'

printf "\033c\033[?25l"

# --- Act 1: Hook (visible from frame 1) ---
printf "\n\n"
printf "  ${B}${WHITE}Stop polling. Start listening.${R}\n\n"
printf "  ${DIM}A Claude Code plugin.${R}\n"
sleep 3.0

printf "\033c"
sleep 0.3

# --- Act 2: What you get ---
printf "\n"
printf "  ${B}${ORANGE}> /el:list${R}\n\n"
sleep 0.6
printf "  ${GREEN}log-tail${R}   ${GREEN}webhook${R}   ${GREEN}ci-watch${R}\n"
printf "  ${GREEN}pr-checks${R}  ${GREEN}file-change${R}\n"
printf "  ${GREEN}webhook-public${R}  ${ORANGE}http-poll${R}\n"
sleep 0.8
printf "\n  ${DIM}7 sources. Drop-in extensible.${R}\n"
sleep 2.0

printf "\033c"
sleep 0.3

# --- Act 3: Natural language ---
printf "\n"
printf "  ${DIM}Just say what you want:${R}\n\n"
sleep 0.6
printf "  ${B}${ORANGE}>${R} ${WHITE}tail the API server logs${R}\n\n"
sleep 1.0
printf "  ${DIM}Background listener starts.${R}\n"
printf "  ${DIM}No sleep loops. Just waits.${R}\n"
sleep 1.8

printf "\033c"
sleep 0.3

# --- Act 4: The payoff ---
printf "\n"
printf "  ${B}${ORANGE}Event received${R}\n\n"
sleep 0.3
printf "  ${GREEN}[INFO]${R}  Connected to postgres\n"
sleep 0.12
printf "  ${GREEN}[INFO]${R}  Listening on :3000\n"
sleep 0.12
printf "  ${YELLOW}[WARN]${R}  Slow query: 2.1s\n"
sleep 0.12
printf "  ${RED}[ERROR]${R} Redis timeout\n"
sleep 1.0
printf "\n  ${WHITE}Claude reacts instantly.${R}\n"
printf "  ${WHITE}No polling delay.${R}\n"
sleep 3.0
