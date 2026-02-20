"""TIER_MAP de domínios acadêmicos + classify_domain().

Extraído integralmente de check_refs.py (Livro Editora Atheneu/scripts/).
~200 domínios classificados em 4 tiers de qualidade.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import Tier

# Tier 1 — Periódicos revisados por pares indexados
# Tier 2 — Fontes institucionais de referência
# Tier 3 — Relatórios técnicos / especializados
# Tier 4 — Notícia / blog / fonte informal

TIER_MAP: dict[str, int] = {
    # ── Tier 1 — Periódicos revisados por pares ──────────────────────────
    "link.springer.com": 1, "springer.com": 1,
    "sciencedirect.com": 1,
    "pmc.ncbi.nlm.nih.gov": 1, "pubmed.ncbi.nlm.nih.gov": 1,
    "scielo.br": 1, "scielo.org": 1, "scielo.org.co": 1,
    "ieeexplore.ieee.org": 1, "doi.org": 1,
    "bmj.com": 1, "bmcpublichealth.biomedcentral.com": 1,
    "biomedcentral.com": 1, "ojrd.biomedcentral.com": 1,
    "journals.plos.org": 1, "plos.org": 1,
    "jmir.org": 1, "mdpi.com": 1,
    "journals.library.columbia.edu": 1,
    "jmphc.com.br": 1, "jmphc.emnuvens.com.br": 1,
    "researchgate.net": 1,
    "tandfonline.com": 1,
    "semanticscholar.org": 1,
    "revistas.usp.br": 1, "rsp.fsp.usp.br": 1,
    "latam.redilat.org": 1,
    "ebooks.iospress.nl": 1,
    "gazetamedica.pt": 1,
    "jisem-journal.com": 1,
    "abccardiol.org": 1,
    "seer.ufrgs.br": 1,
    "fi-admin.bvsalud.org": 1,
    "bjihs.emnuvens.com.br": 1,
    "periodicos.feevale.br": 1,
    "periodicorease.pro.br": 1,
    "rsdjournal.org": 1,
    "revistas.pucsp.br": 1,
    "rbejournal.org": 1,
    "iris.paho.org": 1,
    "acervomais.com.br": 1,
    "revista.cpaqv.org": 1,
    "revsalus.com": 1,
    "arxiv.org": 1,
    "dominiodelasciencias.com": 1,
    "ojs.revistacontemporanea.com": 1,
    "revistas.unphu.edu.do": 1,
    "ascopubs.org": 1,
    "degruyter.com": 1,
    "sciendo.com": 1,
    "journals.sagepub.com": 1,
    "spiedigitallibrary.org": 1,
    "medrxiv.org": 1,
    "frontiersin.org": 1,
    "frontierspartnerships.org": 1,
    "dovepress.com": 1,
    "aacrjournals.org": 1,
    "ashpublications.org": 1,
    "ghspjournal.org": 1,
    "ijic.org": 1,
    "periodicos.ufsc.br": 1,
    "ojs.ufpi.br": 1,
    "e-publicacoes.uerj.br": 1,
    "periodicos.ufpb.br": 1,
    "revistas.ufpr.br": 1,
    "rbgn.fecap.br": 1,
    "ojs.revistagesec.org.br": 1,
    "online.unisc.br": 1,
    "revhosp.org": 1,
    "jhi.sbis.org.br": 1,
    "sol.sbc.org.br": 1,
    "ojs.studiespublicacoes.com.br": 1,
    "revistagt.fpl.emnuvens.com.br": 1,
    "journalhosting.ucalgary.ca": 1,
    "ojs.edicic.org": 1,
    "kluwerlawonline.com": 1,
    "nature.com": 1,
    "thelancet.com": 1,
    "nejm.org": 1,
    "wiley.com": 1,
    "onlinelibrary.wiley.com": 1,
    "academic.oup.com": 1,
    "cell.com": 1,
    "elsevier.com": 1,
    "karger.com": 1,
    "liebertpub.com": 1,
    "cambridge.org": 1,

    # ── Tier 2 — Fontes institucionais de referência ─────────────────────
    "hl7.org": 2,
    "planalto.gov.br": 2,
    "ieps.org.br": 2,
    "saude.gov.br": 2, "gov.br": 2,
    "datasus.gov.br": 2, "ans.gov.br": 2, "anvisa.gov.br": 2,
    "ibge.gov.br": 2, "fiocruz.br": 2, "ipea.gov.br": 2,
    "who.int": 2, "paho.org": 2,
    "himss.org": 2, "anahp.com.br": 2,
    "rnds-guia.saude.gov.br": 2,
    "cetic.br": 2,
    "iess.org.br": 2,
    "cfm.org.br": 2,
    "ministeriodasaude.gov.br": 2,
    "tabnet.datasus.gov.br": 2,
    "ces.ibge.gov.br": 2,
    "pwc.com.br": 2, "pwc.com": 2,
    "direitosnarede.org.br": 2,
    "irisbh.com.br": 2,
    "datasus.saude.gov.br": 2,
    "cofen.gov.br": 2,
    "cdc.gov": 2,
    "publications.iadb.org": 2,
    "nih.gov": 2,
    "europa.eu": 2,
    "worldbank.org": 2,
    "undp.org": 2,
    "oecd.org": 2,

    # ── Tier 3 — Relatórios técnicos / especializados ────────────────────
    "medicinasa.com.br": 3,
    "grandviewresearch.com": 3, "straitsresearch.com": 3,
    "marketsandmarkets.com": 3, "statista.com": 3,
    "mckinsey.com": 3,
    "fiercehealthcare.com": 3,
    "fau.edu": 3,
    "jornal.usp.br": 3,
    "ibanet.org": 3,
    "sindihospa.com.br": 3,
    "drgbrasil.com.br": 3,
    "revistakdea360.com.br": 3,
    "insurtalks.com.br": 3,
    "inova.coop.br": 3,
    "gvaa.com.br": 3,
    "jonuns.com": 3, "jier.org": 3,
    "bdm.unb.br": 3, "repositorio.ufsc.br": 3,
    "repositorio.bc.ufg.br": 3,
    "assets.aboutamazon.com": 3,
    "sincomercio.org.br": 3,
    "periodicosbrasil.emnuvens.com.br": 3,
    "ricsjournal.com": 3,
    "ijprajournal.com": 3,
    "institutojubones.edu.ec": 3,
    "ojs.brazilianjournals.com.br": 3,
    "ojs.jaff.org.br": 3,
    "periodicos.set.edu.br": 3,
    "apm.org.br": 3,
    "cetes.medicina.ufmg.br": 3,
    "proceedings.galoa.com.br": 3,
    "contecsi.tecsi.org": 3,
    "periodicosibepes.org.br": 3,
    "revista.univap.br": 3,
    "revistas.uece.br": 3,
    "deloitte.com": 3,
    "proceedings.blucher.com.br": 3,
    "ayaeditora.com.br": 3,

    # ── Tier 4 — Notícia / blog / fonte informal ────────────────────────
    "sensorweb.com.br": 4,
    "topsaudehub.com.br": 4,
    "saudebusiness.com.br": 4, "saudebusiness.com": 4,
    "azure.microsoft.com": 4, "cloud.google.com": 4,
    "blog.carefy.com.br": 4, "page.carefy.com.br": 4,
    "carefy.com.br": 4,
    "multieducativa.com.br": 4,
    "senior.com.br": 4,
    "saude.ba.gov.br": 4,
    "infrafm.com.br": 4,
    "heroncarlos.com.br": 4,
    "futurecom.com.br": 4, "digital.futurecom.com.br": 4,
    "youtube.com": 4,
    "wp.rededorsaoluiz.com.br": 4,
    "ardigen.com": 4,
    "convergenciadigital.com.br": 4,
    "portalhospitaisbrasil.com.br": 4,
    "eretz.bio": 4,
    "a3data.com.br": 4,
    "omnihospitalar.com.br": 4,
    "maida.health": 4,
    "segs.com.br": 4,
    "futurodasaude.com.br": 4,
    "ctsconsultoria.com.br": 4,
    "onovonormal.blog": 4,
    "grsadv.com.br": 4,
    "klalaw.com.br": 4,
    "revistaft.com.br": 4,
    "saudedigitalnews.com.br": 4,
    "migalhas.com.br": 4,
    "prnewswire.com": 4,
    "jota.info": 4,
    "linkedin.com": 4,
    "medium.com": 4,
    "twitter.com": 4,
    "facebook.com": 4,
    "wikipedia.org": 4,
}


def classify_domain(url: str) -> tuple[str, Tier]:
    """Classifica o domínio de uma URL por tier de qualidade.

    Busca correspondência exata primeiro, depois por sufixo
    (ex: sub.scielo.br → scielo.br → Tier.T1).

    Returns:
        (hostname, Tier) — Tier.UNKNOWN se domínio não classificado.
    """
    if not url:
        return "", Tier.UNKNOWN
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        host = re.sub(r"^www\.", "", host)

        # Correspondência exata
        if host in TIER_MAP:
            return host, Tier(TIER_MAP[host])

        # Busca por sufixo (sub.scielo.br → scielo.br)
        parts = host.split(".")
        for i in range(1, len(parts)):
            suffix = ".".join(parts[i:])
            if suffix in TIER_MAP:
                return host, Tier(TIER_MAP[suffix])

        return host, Tier.UNKNOWN
    except Exception:
        return "", Tier.UNKNOWN
