# GPU Identification & Docker Mapping

In systems with multiple NVIDIA GPUs, identifying which physical card maps to which UUID is critical for consistent deployment.

## GPU Identification Script

Use this script to generate a clean mapping of your hardware vendors, models, and UUIDs.

```bash
#!/bin/bash

# 1. Define formatting
FORMAT="| %-12s | %-10s | %-8s | %-40s |\n"
SEP="+--------------+------------+----------+------------------------------------------+"

# 2. Print Header
echo "$SEP"
printf "$FORMAT" "MANUFACTURER" "MODEL" "MEMORY" "UUID"
echo "$SEP"
  
# 3. Gather and Print Data
nvidia-smi --query-gpu=pci.bus_id,name,memory.total,uuid --format=csv,noheader | while IFS=, read -r bus name mem uuid; do
    # Extract bus ID
    bus_id=$(echo "$bus" | cut -d':' -f2,3)
    
    # Get Manufacturer
    vendor=$(lspci -s "$bus_id" -v | grep "Subsystem:" | cut -d":" -f2- | xargs | awk '{print $1}')
    
    # Clean Model & Memory
    model=$(echo "$name" | sed 's/NVIDIA GeForce //g; s/RTX //g')
    memory=$(echo "$mem" | awk '{print $1/1024 "GB"}')
    
    # Print the row
    printf "$FORMAT" "$vendor" "$model" "$memory" "$uuid"
done

# 4. Print Footer
echo "$SEP"
```

### Example Output

```text
+--------------+------------+----------+------------------------------------------+
| MANUFACTURER | MODEL      | MEMORY   | UUID                                     |
+--------------+------------+----------+------------------------------------------+
| Gigabyte     |  3060      | 12GB     | GPU-5a6bd876-90f0-e6cf-a89e-ed47392ceb59 |
| eVga.com.    |  3060      | 12GB     | GPU-88a93309-9467-dab9-9491-30f298a784f1 |
| ASUSTeK      |  3050      | 6GB      | GPU-f1690852-7dc1-2557-d0f9-93cd6b19b877 |
+--------------+------------+----------+------------------------------------------+
```

## Why use both `NVIDIA_VISIBLE_DEVICES` and `device_ids`?

Docker Compose (and the underlying Docker runtime) does not always consistently respect the simple integer-based schema (`gpu: 0`, `gpu: 1`, etc.). Indices can shift based on driver versions, PCIe bus order, or host reboots.

To ensure a service **always** gets the correct physical hardware, we use a dual-layer approach:

1.  **`deploy.resources.reservations.devices.device_ids`**: This tells the Docker Engine exactly which hardware UUID to reserve and pass through to the container.
2.  **`NVIDIA_VISIBLE_DEVICES` (Environment Variable)**: Many containerized applications (like Frigate, Immich, or Llama-cpp) use this variable to initialize their internal libraries. Setting this explicitly prevents the application from "seeing" other GPUs that might have been accidentally leaked or misindexed by the runtime.

## Environment Variable Usage

Instead of hardcoding these long UUIDs in every `docker-compose.yml`, export them in your `~/.bashrc`:

```bash
export GPU_3060_LONG=GPU-5a6bd876-90f0-e6cf-a89e-ed47392ceb59
export GPU_3060_SHORT=GPU-88a93309-9467-dab9-9491-30f298a784f1
export GPU_3050=GPU-f1690852-7dc1-2557-d0f9-93cd6b19b877
```

Then, use them in your Compose files like this:

```yaml
services:
  frigate:
    environment:
      - NVIDIA_VISIBLE_DEVICES=${GPU_3050}
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              device_ids: ['${GPU_3050}']
              capabilities: [gpu, compute, video, utility]
```
