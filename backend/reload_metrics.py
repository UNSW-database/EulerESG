#!/usr/bin/env python3
"""
Reload SASB metrics for Dell report analysis
"""
import requests
import json

# API endpoint
url = "http://127.0.0.1:8000/api/reprocess"

# Make API call to reprocess with correct parameters
response = requests.post(url, json={
    "file_id": "b316af4c-15a4-4f21-b066-0abf5aac706b",
    "framework": "SASB",
    "industry": "Technology & Communications", 
    "semiIndustry": "Software & IT Services"
})

print(f"Status: {response.status_code}")
print(f"Response: {response.text}")