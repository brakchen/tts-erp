# Media browser fixture

`TEST_preview.webm` is a locally generated solid-blue test clip. It contains no
real user media or third-party source material. Browser tests upload these
versioned bytes; FFmpeg is not a test or application runtime dependency.

- Container / codec: WebM / VP8, no audio
- Dimensions / duration: 320 × 180 pixels, 1 second (10 fps)
- Size: 884 bytes
- SHA-256: `13e64cdde5c224ce66bc324db1246b550fba532fb5e44fa7cc6eddd3a32e1221`
- Generated locally on 2026-11-06; metadata checked with `ffprobe`

Generation command:

```bash
ffmpeg -hide_banner -loglevel error \
  -f lavfi -i 'color=c=0x1274b4:s=320x180:r=10:d=1' \
  -an -c:v libvpx -b:v 40k \
  -fflags +bitexact -flags:v +bitexact -map_metadata -1 \
  -y TEST_preview.webm
```
