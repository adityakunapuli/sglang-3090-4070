# GPU Identification & Docker Mapping

In systems with multiple NVIDIA GPUs, identifying which physical card maps to which UUID is critical for consistent
deployment.

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

Docker Compose (and the underlying Docker runtime) does not always consistently respect the simple integer-based schema
(`gpu: 0`, `gpu: 1`, etc.). Indices can shift based on driver versions, PCIe bus order, or host reboots.

To ensure a service **always** gets the correct physical hardware, we use a dual-layer approach:

1. **`deploy.resources.reservations.devices.device_ids`**: This tells the Docker Engine exactly which hardware UUID to
   reserve and pass through to the container.
2. **`NVIDIA_VISIBLE_DEVICES` (Environment Variable)**: Many containerized applications (like Frigate, Immich, or
   Llama-cpp) use this variable to initialize their internal libraries. Setting this explicitly prevents the application
   from "seeing" other GPUs that might have been accidentally leaked or misindexed by the runtime.

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
              device_ids: [ '${GPU_3050}' ]
              capabilities: [ gpu, compute, video, utility ]
```              

## PCIe Slot Bandwidth Analysis

The system runs an **ASRock Z690 Steel Legend** motherboard. Below is the mapping of each GPU's slot location, its
theoretical maximum PCIe slot capabilities, and its peak historical bandwidth usage (measured over the last 30 days):

### Motherboard PCIe Slot Configuration & Maximum Capabilities

* **Slot 1 (PCIE2)**: PCIe 5.0 x16 (physical capability of **~63.0 GB/s**). Runs at **PCIe 4.0 x16** speeds (**~31.50 GB/s**) when populated with an RTX 3000-series card.
* **Slot 3 (PCIE3)**: PCIe 4.0 x16 (physical). Runs at **PCIe 4.0 x4** speeds.
    * *Theoretical Bandwidth Limit:* **~7.87 GB/s** (7.3 GiB/s)
* **Slot 5 (PCIE5)**: PCIe 3.0 x16 (physical). Runs at **PCIe 3.0 x4** speeds.
    * *Theoretical Bandwidth Limit:* **~3.94 GB/s** (3.67 GiB/s)

### GPU Bandwidth Usage (Last 30 Days Peak)

| GPU                           | Physical Location  | PCIe Link Speed | Slot Max Limit | Historical Peak RX            | Historical Peak TX            | Historical Peak Total          |
|:------------------------------|:-------------------|:----------------|:---------------|:------------------------------|:------------------------------|:-------------------------------|
| **RTX 3060 (0)**<br>*(Long)*  | **Slot 1** (PCIE2) | PCIe 5.0 x16 (runs at 4.0 x16) | 31.50 GB/s (Slot max is ~63.0 GB/s) | **9.20 GB/s** *(8,778 MiB/s)* | **5.69 GB/s** *(5,423 MiB/s)* | **14.89 GB/s** *(14,201 MiB/s)* |
| **RTX 3060 (1)**<br>*(Short)* | **Slot 3** (PCIE3) | PCIe 4.0 x4     | 7.87 GB/s      | **6.77 GB/s** *(6,455 MiB/s)* | **5.04 GB/s** *(4,805 MiB/s)* | **11.81 GB/s** *(11,260 MiB/s)* |
| **RTX 3050 (2)**              | **Slot 5** (PCIE5) | PCIe 3.0 x4     | 3.94 GB/s      | **2.76 GB/s** *(2,630 MiB/s)* | **1.56 GB/s** *(1,485 MiB/s)* | **4.32 GB/s** *(4,115 MiB/s)*   |

## Active References in Configuration Files

Below is a list of active files and line numbers referencing these environment variables, making it easy to rename or migrate them to more generic names (e.g., `GPU_SLOT_1`, `GPU_SLOT_2`, `GPU_SLOT_3`):

### 1. User Shell Profile
* **[~/.bashrc](file:///home/maradmin/.bashrc#L10-L12)**:
  * `GPU_3060_LONG` (maps currently to the Gigabyte card in Slot 1)
  * `GPU_3060_SHORT` (maps currently to the EVGA card in Slot 3)
  * `GPU_3050` (maps to the ASUS card in Slot 5)

### 2. Stack Configurations
* **[llama-cpp/docker-compose.yaml](llama-cpp/docker-compose.yaml#L36-L37)**:
  * Uses `${GPU_3060_LONG:?GPU_3060_LONG_NOT_SET}` (Line 36)
  * Uses `${GPU_3060_SHORT:?GPU_3060_SHORT_NOT_SET}` (Line 37)
* **[llama-swap/docker-compose.yaml](llama-swap/docker-compose.yaml#L28-L29)**:
  * Uses `${GPU_3060_LONG:?GPU_3060_LONG_NOT_SET}` (Line 28)
  * Uses `${GPU_3060_SHORT:?GPU_3060_SHORT_NOT_SET}` (Line 29)
* **[immich/docker-compose.yaml](immich/docker-compose.yaml#L18)**:
  * Uses `${GPU_3050:?GPU_3050_UUID_NOT_SET}` (Lines 18, 49, 87)
* **[jellyfin/docker-compose.yml](jellyfin/docker-compose.yml#L10)**:
  * Uses `${GPU_3050:?GPU_3050_UUID_NOT_SET}` (Lines 10, 34)
* **[frigate/docker-compose.yml](frigate/docker-compose.yml#L13)**:
  * Uses `${GPU_3050:?GPU_3050_UUID_NOT_SET}` (Lines 13, 15, and commented out Line 41)
