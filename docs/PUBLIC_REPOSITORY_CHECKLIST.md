# Public repository checklist

Use this checklist before changing `Sllay/Amora-Chibi` from private to public.

## Required

- [x] Proprietary `LICENSE` present; project remains source-available, not open source.
- [x] `SECURITY.md` forbids committing runtime secrets, databases and signing keys.
- [x] `.gitignore` excludes Amora runtime data, environment files, databases and private signing material.
- [x] Current `main` tree checked for obvious GitHub/Groq/Gemini credential prefixes; no real credential was found in the checked patterns.
- [x] Current `main` tree contains no tracked database, `.env`, keystore or private-key file.
- [ ] Replace the Android polish branch's committed debug keystore with a newly rotated key supplied only through GitHub Actions Secrets.
- [ ] Configure repository secret `AMORA_DEBUG_KEYSTORE_B64` with the rotated development signing keystore.
- [ ] Confirm no other active branch intentionally contains private runtime exports or credentials.
- [ ] Change repository visibility to public in GitHub Settings.
- [ ] Re-run Core and Android Actions after the visibility change.

## Android signing rule

The stable V1 development APK may use a dedicated development signing identity so later APKs can update in place. The private key must never be tracked by Git. CI restores it from `AMORA_DEBUG_KEYSTORE_B64` only inside the temporary runner.

If a signing key is committed, it is considered compromised and must never become the stable update identity. Removing the file from the latest commit is not sufficient to make that key trustworthy again.

## Audit scope note

The pre-publication checks performed through the GitHub integration cover the active repository tree and known active refs/patterns available through the integration. They are not a cryptographic proof that no arbitrary historical blob ever contained sensitive material. Any known credential that was previously committed must be rotated regardless of later deletion.
