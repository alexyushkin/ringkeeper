# Test fixtures

`voice-works.ogg`: 3.3 s of synthesized speech ("Voice works. Add a line to the
notes."), OGG/Opus like a Telegram voice note. Made with espeak-ng and ffmpeg
in a throwaway Debian container:

```bash
espeak-ng -v en-us -s 140 -w v.wav "Voice works. Add a line to the notes."
ffmpeg -i v.wav -ac 1 -ar 48000 -c:a libopus -b:a 24k -application voip voice-works.ogg
```
