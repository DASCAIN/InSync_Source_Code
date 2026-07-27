#!/usr/bin/env bash
# Increase /tmp tmpfs size to 5GB dynamically
sudo mount -o remount,size=5G /tmp