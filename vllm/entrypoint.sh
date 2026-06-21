#!/bin/sh
exec vllm serve $(cat /app/args.txt)