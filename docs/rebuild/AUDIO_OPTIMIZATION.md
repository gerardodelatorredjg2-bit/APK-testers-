# Audio optimization plan

Re-encode existing OGG assets only on a disposable build copy. Keep the original recovery archive untouched and preserve the exact resource paths and import sidecars. The encoder uses Vorbis quality 4 by default, but replaces a file only when the encoded result is smaller. Failed or larger encodes keep the original.

An initial trial on the 11 largest tracks reduced 70.52 MiB to 35.62 MiB (49.5%). This is a measured sample, not a guaranteed saving for the whole library. Review the complete per-track CSV and listen to music/effects before release.
