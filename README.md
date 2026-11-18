# APK Testers

Herramientas de análisis estático para comprobar compatibilidad ABI de paquetes Android. El objetivo inicial es investigar si una copia legítima de **Papers, Please** puede ejecutarse en un dispositivo ARMv7.

## Estado

- [x] Repositorio privado y herramientas de inspección inicial.
- [ ] Obtener una copia legítima del paquete Android.
- [ ] Inventariar APK base y todos los APK divididos (splits).
- [ ] Comprobar ABI, bibliotecas ELF, SDK mínimo y firma.
- [ ] Determinar si existe soporte real para `armeabi-v7a`.

**No se ha descargado, instalado ni modificado ningún APK.** No se almacena aquí el juego ni ningún otro APK con copyright. Los archivos binarios de entrada deben permanecer fuera del repositorio.

## Por qué no basta con renombrar una biblioteca

Android separa las bibliotecas nativas por ABI. Para ARM de 32 bits, la ruta habitual es `lib/armeabi-v7a/`; para ARM de 64 bits, `lib/arm64-v8a/`. Un ELF AArch64 no se convierte en ARM de 32 bits cambiando su nombre o carpeta. Si el paquete solo trae código ARM64, hace falta una compilación ARMv7 compatible del código nativo y de sus dependencias.

En APKs distribuidos como Android App Bundle, las bibliotecas pueden estar en un split de ABI y no en el APK base. Por eso hay que inspeccionar el conjunto completo.

## Herramienta

Requiere Python 3.9+ y solo usa la biblioteca estándar:

```bash
python tools/inspect_apk.py /ruta/al/archivo.apk
```

El informe muestra SHA-256, integridad ZIP, bibliotecas `.so`, ABI declaradas en las rutas y arquitectura ELF real. La herramienta no instala ni ejecuta el APK.

Para inspeccionar un paquete ya instalado desde un dispositivo Android con ADB:

```bash
adb shell pm path com.llc3909.papersplease
```

Ese comando devuelve las rutas de todos los APK instalados (base y splits). Extrae cada ruta con `adb pull` y analiza cada archivo por separado. Solo hazlo con una copia obtenida legítimamente y en un dispositivo/cuenta que tenga derecho a usarla.

## Fuentes

- [Papers, Please en Google Play](https://play.google.com/store/apps/details?id=com.llc3909.papersplease)
- [Documentación Android: compatibilidad de 64 bits y carpetas ABI](https://developer.android.com/google/play/requirements/64-bit)
- [OWASP MASTG: extracción e inspección de bibliotecas nativas](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0157/)
