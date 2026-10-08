# Android — V0.2 Audio Bridge

Cliente Android nativo da Amora. Nesta fase ele é deliberadamente pequeno: captura microfone, roda VAD local, envia apenas segmentos de fala ao Core Python em `127.0.0.1:8765` e exibe o resultado via logs/notificação.

## Stack congelada nesta fase

- AGP 9.4.0
- Gradle 9.6
- JDK 17
- compile/target SDK 36
- min SDK 26
- Kotlin integrado ao AGP 9 (sem plugin Kotlin Android separado)
- sherpa-onnx 1.13.8 AAR oficial
- Silero VAD local

Os binários nativos **não são versionados**. Rode:

```bash
bash android/tools/prepare-deps.sh
```

O script baixa AAR/modelo dos releases oficiais e valida SHA-256 antes do build.

## Pareamento

Com o Core rodando no Termux:

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src
amora serve
```

Em outro terminal:

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src
amora pair
```

Digite no app o código de 6 dígitos. Ele expira em 5 minutos, aceita no máximo 5 tentativas e é consumido no primeiro uso correto. O token longo recebido é cifrado pelo Android Keystore.

## Build

O CI usa JDK 17 + Gradle 9.6 e publica o APK debug como artifact. Para build manual, prepare as dependências e rode Gradle 9.6:

```bash
gradle -p android :app:assembleDebug
```

O APK fica em `android/app/build/outputs/apk/debug/app-debug.apk`.

## Microfone

O serviço usa `foregroundServiceType="microphone"`. Em Android 14+ ele deve ser iniciado enquanto o app está visível/tem permissão de uso do microfone; por isso a captura começa somente pelo botão da Activity.

`SherpaSpeechSegmenter` é o caminho normal. Se o JNI/modelo falhar, `EnergySpeechSegmenter` entra como fallback para o Core continuar funcional.
