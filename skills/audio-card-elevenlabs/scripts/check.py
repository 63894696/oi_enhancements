import os, sys, urllib.request, urllib.error

KEY = os.environ.get("ELEVENLABS_API_KEY", "")


def main():
    if not KEY:
        print("[fail] ELEVENLABS_API_KEY 未设")
        sys.exit(1)
    try:
        req = urllib.request.Request("https://api.elevenlabs.io/v1/user",
                                     headers={"Authorization": f"Bearer {KEY}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            print(f"[ok] ElevenLabs reachable, HTTP {r.status}")
            sys.exit(0)
    except urllib.error.HTTPError as e:
        print(f"[fail] HTTP {e.code} — key 可能无效")
    except Exception as e:
        print(f"[fail] Network: {e}")
    sys.exit(1)


if __name__ == "__main__":
    main()