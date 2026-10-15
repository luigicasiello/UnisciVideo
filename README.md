# UnisciVideo

Applicazione Windows standalone per unire due o più clip in un solo video MP4. L’interfaccia e il flusso riprendono lo stile di **ComprimiPDF**, con una mascotte illustrata creata dalla foto fornita per il progetto.

## Funzioni

- aggiunta multipla e drag & drop delle clip;
- ordinamento con i pulsanti **Su** e **Giù**;
- supporto per MP4, MOV, AVI, MKV, WebM, WMV, MPEG, MTS/M2TS, 3GP e TS;
- normalizzazione automatica di risoluzione, proporzioni, frame rate e audio;
- rilevamento e rimozione automatica dei fotogrammi neri già presenti ai bordi interni delle clip;
- profili Alta qualità, Bilanciata e File più leggero;
- formati orizzontale Full HD/HD, verticale, quadrato o dimensioni della prima clip;
- pausa nera facoltativa e regolabile tra le clip, disattivata per impostazione predefinita;
- elaborazione in background, avanzamento e annullamento;
- output H.264/AAC compatibile con Windows, telefoni, TV e browser moderni;
- originali sempre lasciati intatti.

Le clip con proporzioni diverse vengono adattate senza deformazioni; l’eventuale spazio restante è riempito in nero. Alle clip prive di audio viene aggiunta una traccia silenziosa, così la sincronizzazione dell’intero video rimane stabile.

## Avvio dal codice

Con Python 3.11 installato:

```powershell
.\run.ps1
```

## Creazione dell’eseguibile standalone

```powershell
.\build.ps1
```

L’eseguibile portabile viene creato in `dist\UnisciVideo.exe`. Include FFmpeg e non richiede Python né altri programmi sul computer di destinazione.

Il repository non contiene il binario FFmpeg né l’eseguibile compilato. Durante la build, `imageio-ffmpeg` fornisce automaticamente FFmpeg e lo script lo copia nella cartella locale `bin` prima di creare l’EXE.

## Test

```powershell
python -m unittest discover -s tests -v
```

## Note sulle licenze

L’app usa PySide6 (LGPL) e FFmpeg. Il binario FFmpeg incluso è una build GPLv3 di Gyan.dev con libx264. Prima di distribuire pubblicamente l’eseguibile occorre rispettare le relative licenze e rendere disponibili le informazioni e il codice sorgente richiesti. Per uso personale locale il progetto può essere compilato e usato direttamente.

Se il certificato locale attendibile è già configurato, `build.ps1` firma automaticamente ogni nuova build. La prima configurazione richiede un’azione esplicita dell’utente. Per preparare il certificato:

```powershell
.\setup-local-signing.ps1
```

Installare manualmente `certificates\UnisciVideo-Local-Publisher.cer` negli archivi **Autorità di certificazione radice attendibili** ed **Editori attendibili** dell’utente corrente, quindi eseguire:

```powershell
.\sign-executable.ps1 -Path .\dist\UnisciVideo.exe
```

È una firma autofirmata locale: non sostituisce un certificato commerciale con reputazione SmartScreen.

