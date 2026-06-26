<h1 align="center">Fec_AdE</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/License-MIT-green?logo=opensourceinitiative" alt="MIT License">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?logo=windows" alt="Cross-platform">
  <img src="https://github.com/andrewilly/Fec_AdE/actions/workflows/build.yml/badge.svg" alt="Build">
</p>

**Interfaccia CLI per i servizi web dell'Agenzia delle Entrate.**

Consente il download di fatture elettroniche, F24, corrispettivi,
dichiarazioni dei redditi e documenti dal Cassetto Fiscale — tutto
da terminale, senza browser.

---

## Requisiti

- Python 3.9+
- Credenziali **Entratel** o **Fisconline**
- Una **delega attiva** per operare per conto terzi

## Installazione

```bash
git clone https://github.com/andrewilly/Fec_AdE.git
cd Fec_AdE
pip install -r requirements.txt
cp example.env ~/.fec_ade/config.env
# Modifica con le tue credenziali
nano ~/.fec_ade/config.env
```

## Utilizzo

### Menu interattivo

```bash
python menu.py
```

### CLI

```bash
# Scarica fatture via Incaricato
python cli.py fatture --incaricato

# F24 bolli per un cliente
python cli.py f24 bolli --piva 01234567890 --anno 2025 --trimestre 1

# Dichiarazione dei redditi
python cli.py cassetto dichiarazioni --piva CF --anno 2025 --tipo RED

# Estrai elenco deleghe
python cli.py deleghe estrai

# Scarica tutto (fatture + corrispettivi)
python cli.py tutto --all --anno 2025
```

## Corrispettivi

I corrispettivi vengono salvati in formato **Excel (.xlsx)** con
le seguenti colonne: ID Invio, Matricola dispositivo, Tipo dispositivo,
Partita IVA, Data rilevazione, Annullati, Resi, Imponibile Giornata,
Imposta giornata, Aliquota 4%, Aliquota 5%, Aliquota 10%,
Aliquota 22%, Esente.

Per i record con aliquota mista (es. 22% + Esente) viene applicato
un algoritmo euristico che scompone l'importo totale in base alle
aliquote standard.

---

## ⚠️ Disclaimer

**Questo software non è affiliato, approvato o mantenuto dall'Agenzia
delle Entrate.**

L'utilizzo di questo software comporta la connessione ai servizi web
dell'Agenzia delle Entrate tramite le credenziali personali
dell'utente (Entratel/Fisconline).

**L'UTENTE SI ASSUME OGNI RESPONSABILITÀ:**

1. **Verifica dei dati**: L'accuratezza, completezza e correttezza
   dei dati scaricati è esclusiva responsabilità dell'utilizzatore.
   I dati devono essere verificati confrontandoli con il portale
   ufficiale dell'Agenzia delle Entrate.

2. **Corrispettivi**: La ripartizione per aliquota IVA dei corrispettivi
   è basata su algoritmi euristici. L'autore non garantisce che la
   suddivisione corrisponda esattamente ai dati ufficiali.

3. **Credenziali**: Le credenziali di accesso sono gestite localmente
   e non vengono trasmesse a terze parti. L'utente è responsabile
   della loro custodia.

4. **Blocchi e sanzioni**: L'autore declina ogni responsabilità per
   blocchi dell'account, sanzioni o qualsiasi danno derivante
   dall'uso di questo software.

5. **Termini di servizio**: L'utilizzatore si impegna a rispettare
   i termini di servizio dei portali dell'Agenzia delle Entrate.

**Usando questo software l'utente accetta integralmente i termini
del presente disclaimer. In caso di mancata accettazione, non
utilizzare il software.**
