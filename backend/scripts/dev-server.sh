#!/usr/bin/env bash

while true; do
    uv run dev

    echo
    echo "Press any key to restart the server..."
    echo

    read -n1 -s
done
