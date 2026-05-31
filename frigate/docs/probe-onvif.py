#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "python-dotenv",
#     "onvifscout",
#     "onvif-python",
#     "rich",
#     "zeep",
# ]
# ///

import os

from dotenv import load_dotenv, find_dotenv
from onvif import ONVIFDiscovery, ONVIFClient
from rich.console import Console
from zeep import helpers

if '__file__' not in globals():
    os.chdir('docker/frigate/docs')

console = Console()

if env_path := find_dotenv('.env'):
    load_dotenv(env_path, verbose=True, override=True, encoding="utf-8")
    print(f'Env file: {env_path}')
else:
    raise Exception("No .env file found")

CREDENTIALS = [
    (os.getenv('CAMERA_USERNAME'), os.getenv('CAMERA_PASSWORD')),
]

print(CREDENTIALS)


def clean_dict(d):
    """Aggressively prunes empty, False, and useless ONVIF boilerplate."""
    if isinstance(d, dict) or str(type(d)) == "<class 'collections.OrderedDict'>":
        cleaned = {}
        for k, v in d.items():
            # Skip useless ONVIF boilerplate keys
            if k in ['_value_1', '_attr_1', 'UseCount', 'token', 'fixed'] or k.startswith('xmlns'):
                continue

            cleaned_v = clean_dict(v)

            # Prune empty or unhelpful values
            if cleaned_v in [None, [], {}, "", False]:
                continue

            # Simplify verbose ONVIF URIs if they are just string values
            if isinstance(cleaned_v, str) and cleaned_v.startswith('http://www.onvif.org/'):
                cleaned_v = cleaned_v.split('/')[-1]

            # Flatten 'Extension' if it just contains a single dict to reduce nesting
            if k in ['Extension', 'Extensions'] and isinstance(cleaned_v, dict):
                for ext_k, ext_v in cleaned_v.items():
                    cleaned[ext_k] = ext_v
                continue

            cleaned[k] = cleaned_v
        return cleaned if cleaned else None

    elif isinstance(d, list):
        cleaned_list = [clean_dict(i) for i in d]
        cleaned_list = [i for i in cleaned_list if i not in [None, [], {}, ""]]
        return cleaned_list if cleaned_list else None

    elif hasattr(d, '__class__') and 'Element' in str(d.__class__):
        return None  # Strip raw XML elements entirely

    else:
        return d


def format_event_topics(topic_set, prefix=""):
    """Recursively flattens the Event Topic Map into a readable list of supported events."""
    topics = []
    if not isinstance(topic_set, dict):
        return topics

    for key, value in topic_set.items():
        if key in ['documentation', 'MessageDescription', 'TopicExpressionDialect', 'MessageContentFilterDialect',
                   'ProducerPropertiesFilterDialect', 'MessageContentSchemaLocation', 'TopicNamespaceLocation',
                   'FixedTopicSet', 'TopicSet']:
            continue

        current_path = f"{prefix}/{key}" if prefix else key

        # In ONVIF, topics are often marked by a dict containing a boolean 'topic' attribute or similar nested
        # structures.
        # If it has nested dicts, we recurse.
        if isinstance(value, dict):
            # Check if this node itself is a topic leaf
            is_topic = False
            for k, v in value.items():
                if 'topic' in k and v is True:
                    is_topic = True
                    break

            if is_topic:
                topics.append(current_path)

            # Continue deeper
            sub_topics = format_event_topics(value, current_path)
            topics.extend(sub_topics)

    return sorted(list(set(topics)))


def summarize_profile(p):
    """Extracts a clean, high-signal summary of a Media Profile."""
    summary = {}

    # Video
    vec = p.get('VideoEncoderConfiguration', {})
    if vec:
        enc = vec.get('Encoding', 'Unknown')
        res = vec.get('Resolution', {})
        w, h = res.get('Width', '?'), res.get('Height', '?')
        rc = vec.get('RateControl', {})
        fps = rc.get('FrameRateLimit', '?')
        kbps = rc.get('BitrateLimit', '?')
        summary['Video'] = f"{enc} | {w}x{h} | {fps} FPS | {kbps} kbps"

    # Audio (Microphone / Encoding)
    aec = p.get('AudioEncoderConfiguration', {})
    if aec:
        enc = aec.get('Encoding', 'Unknown')
        sr = aec.get('SampleRate', '?')
        if isinstance(sr, int): sr = f"{sr / 1000}kHz"
        kbps = aec.get('Bitrate', '?')
        summary['Audio (Mic)'] = f"{enc} | {sr} | {kbps} kbps"

    # Audio (Speaker / Decoding / Two-Way)
    # Clean_dict now flattens Extension, so AudioOutputConfiguration might be at the top level
    aoc = p.get('AudioOutputConfiguration')
    adc = p.get('AudioDecoderConfiguration')
    if aoc or adc:
        out_str = "Supported"
        if isinstance(aoc, dict) and 'OutputLevel' in aoc:
            out_str += f" (Vol: {aoc['OutputLevel']})"
        if isinstance(aoc, dict) and 'SendPrimacy' in aoc:
            out_str += f" [{aoc['SendPrimacy'].split('/')[-1]}]"
        summary['Audio (Speaker)'] = out_str

    # Analytics / Rules
    vac = p.get('VideoAnalyticsConfiguration', {})
    rules = []
    if vac:
        rec = vac.get('RuleEngineConfiguration', {}).get('Rule', [])
        if isinstance(rec, list):
            for r in rec:
                rules.append(r.get('Type', '').split(':')[-1])
    if rules:
        summary['Analytics'] = ", ".join(rules)

    return summary


def safe_serialize(obj):
    try:
        serialized = helpers.serialize_object(obj)
        return clean_dict(serialized)
    except Exception:
        return None


def print_human_readable(data, indent=0):
    """Recursively prints dictionaries and lists in a clean, YAML-like human-readable format."""
    spacing = "  " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                console.print(f"{spacing}[cyan]{k}:[/cyan]")
                print_human_readable(v, indent + 1)
            else:
                console.print(f"{spacing}[cyan]{k}:[/cyan] {v}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                first = True
                for k, v in item.items():
                    prefix = f"{spacing}• " if first else f"{spacing}  "
                    if isinstance(v, (dict, list)):
                        console.print(f"{prefix}[cyan]{k}:[/cyan]")
                        print_human_readable(v, indent + 2)
                    else:
                        console.print(f"{prefix}[cyan]{k}:[/cyan] {v}")
                    first = False
            elif isinstance(item, list):
                console.print(f"{spacing}•")
                print_human_readable(item, indent + 2)
            else:
                console.print(f"{spacing}• {item}")
    else:
        console.print(f"{spacing}{data}")


def dump_section(title, data, style="bold cyan"):
    cleaned_data = safe_serialize(data)
    if not cleaned_data:
        return

    console.print()
    console.rule(f"[{style}]{title}[/{style}]", align="left")
    print_human_readable(cleaned_data)


def try_call(service, method_name, *args, **kwargs):
    try:
        method = getattr(service, method_name)
        return method(*args, **kwargs)
    except Exception:
        return None


def deep_probe(client, name):
    console.print()
    console.rule(f"[bold magenta]PROBING: {name}[/bold magenta]", characters="=")

    dev = client.devicemgmt()
    dump_section("DEVICE: Info", try_call(dev, "GetDeviceInformation"))
    dump_section("DEVICE: Network Interfaces", try_call(dev, "GetNetworkInterfaces"))
    dump_section("DEVICE: Network Protocols", try_call(dev, "GetNetworkProtocols"))
    dump_section("DEVICE: Hostname", try_call(dev, "GetHostname"))
    dump_section("DEVICE: NTP", try_call(dev, "GetNTP"))

    # Events summary
    try:
        events = client.events()
        props = try_call(events, "GetEventProperties")
        if props:
            cleaned_props = safe_serialize(props)
            if cleaned_props and 'TopicSet' in cleaned_props:
                topics = format_event_topics(cleaned_props['TopicSet'])
                if topics:
                    console.print()
                    console.rule("[bold yellow]EVENTS: Supported Topics[/bold yellow]", align="left")
                    for t in topics:
                        console.print(f"  - [green]{t}[/green]")
    except Exception:
        pass

    # Media Profiles summary
    media = client.media()
    profiles_raw = try_call(media, "GetProfiles") or []

    # Audio queries
    dump_section("AUDIO: Sources (Microphones)", try_call(media, "GetAudioSources"))
    dump_section("AUDIO: Outputs (Speakers)", try_call(media, "GetAudioOutputs"))

    if profiles_raw:
        console.print()
        console.rule("[bold green]MEDIA: Profiles Summary[/bold green]", align="left")

        for p in profiles_raw:
            cleaned_p = safe_serialize(p)
            if not cleaned_p: continue

            name = cleaned_p.get('Name', 'Unknown')
            summary = summarize_profile(cleaned_p)

            console.print(f"\n[bold]{name}[/bold]")
            for k, v in summary.items():
                console.print(f"  [cyan]{k}:[/cyan] {v}")

            # Stream URIs
            for protocol in ["RTSP", "HTTP"]:
                uri = try_call(media, "GetStreamUri",
                               StreamSetup={"Stream": "RTP-Unicast", "Transport": {"Protocol": protocol}},
                               ProfileToken=p.token)
                if uri:
                    console.print(f"  [cyan]{protocol} URI:[/cyan] {uri.Uri}")

            snapshot = try_call(media, "GetSnapshotUri", ProfileToken=p.token)
            if snapshot:
                console.print(f"  [cyan]Snapshot URI:[/cyan] {snapshot.Uri}")

    # Imaging
    try:
        img = client.imaging()
        video_sources = try_call(media, "GetVideoSources") or []
        for vs in video_sources:
            dump_section(f"IMAGING: Options ({vs.token})", try_call(img, "GetOptions", VideoSourceToken=vs.token))
    except Exception:
        pass

    # PTZ
    try:
        ptz = client.ptz()
        nodes_raw = try_call(ptz, "GetNodes")
        if nodes_raw:
            cleaned_nodes = safe_serialize(nodes_raw)
            if cleaned_nodes:
                console.print()
                console.rule("[bold blue]PTZ: Supported Features[/bold blue]", align="left")
                for node in cleaned_nodes:
                    name = node.get('Name', 'Unknown')
                    presets = node.get('MaximumNumberOfPresets', 0)
                    home = node.get('HomeSupported', False)
                    spaces = node.get('SupportedPTZSpaces', {})
                    has_pan_tilt = ('AbsolutePanTiltPositionSpace' in spaces or 'ContinuousPanTiltVelocitySpace' in
                                    spaces)
                    has_zoom = 'AbsoluteZoomPositionSpace' in spaces or 'ContinuousZoomVelocitySpace' in spaces

                    features = []
                    if has_pan_tilt: features.append("Pan/Tilt")
                    if has_zoom: features.append("Zoom")
                    if presets > 0: features.append(f"{presets} Presets")
                    if home: features.append("Home Position")

                    console.print(f"  [bold]{name}:[/bold] {', '.join(features) if features else 'None'}")
    except Exception:
        pass


def discover_and_probe():
    console.print("[yellow]Scanning network for ONVIF devices...[/yellow]")
    discovery = ONVIFDiscovery(timeout=5)
    devices = discovery.discover()

    if not devices:
        console.print("[red]No devices found.[/red]")
        return

    for dev in devices:
        host, port = dev['host'], dev['port']
        name = next((s.split('/')[-1] for s in dev.get('scopes', []) if 'name' in s.lower()), "Unknown")

        auth_success = False
        for user, pwd in CREDENTIALS:
            if not user or not pwd: continue
            try:
                client = ONVIFClient(host, port, user, pwd)
                client.devicemgmt().GetDeviceInformation()
                
                auth_success = True
                
                deep_probe(client, name)
                break
            except Exception:
                continue
                
        if not auth_success:
            console.print(f"  [red]Failed to authenticate {name} with any known credentials.[/red]")


if __name__ == "__main__":
    discover_and_probe()
