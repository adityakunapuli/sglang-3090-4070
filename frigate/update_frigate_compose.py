import yaml

with open('../frigate/docker-compose.yml', 'r') as f:
    compose = yaml.safe_load(f)

frigate = compose['services']['frigate']

# Remove NVIDIA runtime
if 'runtime' in frigate and frigate['runtime'] == 'nvidia':
    del frigate['runtime']

# Remove deploy block (which contains GPU reservation)
if 'deploy' in frigate:
    del frigate['deploy']

# Remove GPU related environment variables
if 'environment' in frigate:
    frigate['environment'] = [
        env for env in frigate['environment']
        if not (env.startswith('NVIDIA_VISIBLE_DEVICES=') or 
                env.startswith('NVIDIA_DRIVER_CAPABILITIES=') or 
                env.startswith('CUDA_VISIBLE_DEVICES=') or
                env.startswith('LIBVA_DRIVER_NAME='))
    ]

# Keep stable-tensorrt or switch to stable standard
# ghcr.io/blakeblackshear/frigate:stable-tensorrt
if 'image' in frigate and 'tensorrt' in frigate['image']:
    frigate['image'] = frigate['image'].replace('-tensorrt', '')

with open('../frigate/docker-compose.yml', 'w') as f:
    yaml.dump(compose, f, sort_keys=False, default_flow_style=False)
