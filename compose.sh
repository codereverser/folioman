#!/bin/bash
docker compose down -v
rm -rf ui/.nuxt ui/dist
cp api/env.example api/.env
docker compose up --build
