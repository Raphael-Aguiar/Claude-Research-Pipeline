# Decisões Arquiteturais — Pipeline de Pesquisa Acadêmica

Registro de todas as decisões tomadas no design e implementação do pipeline.
Insumo para futura publicação sobre o processo.


## Decisões do Autor

| # | Data | Decisão | Alternativa rejeitada | Justificativa |
|---|---|---|---|---|
| D1 | 2026-02-18 | Duas modalidades (pesquisa-base vs revisão-literatura) usando mesmo pipeline | Pipeline separado por modalidade | São produtos diferentes (intermediário vs final), mas a infraestrutura técnica (scripts Python) é a mesma. Evita duplicação de código. |
| D2 | 2026-02-18 | Gestão de referências em 3 camadas (.bib → Index → Zotero) | Zotero desde o início | O .bib é subproduto natural da verificação CrossRef — os metadados já existem. Zotero seria conveniência futura, não dependência. |
| D3 | 2026-02-18 | MVP = pipeline completo (10 etapas) | MVP mínimo (apenas busca + verificação) | As etapas são relativamente simples individualmente; o valor está no pipeline completo. |
| D4 | 2026-02-18 | Arquitetura híbrida: Skills (Markdown) + Tools (Python) | Apenas scripts Python | Skills são instruções para o Claude; Tools são automação. Skills documentam o "porquê" e "quando"; Tools fazem o "como". |
| D5 | 2026-02-18 | Deploy local primeiro, VPS depois | Cloud desde o início | Simplifica desenvolvimento e debugging. VPS não é necessário para o MVP. |
| D6 | 2026-02-18 | Documentação completa para futura publicação | Documentação mínima | O autor pretende publicar artigo sobre o processo. Documentar decisões e metodologia é investimento, não overhead. |


## Decisões Técnicas

| # | Data | Decisão | Alternativa rejeitada | Justificativa |
|---|---|---|---|---|
| T1 | 2026-02-18 | TIER_MAP extraído integralmente de check_refs.py | Reescrever do zero | ~200 domínios já classificados empiricamente na auditoria de 288 refs. Código testado. |
| T2 | 2026-02-18 | 4 subtipos de revisão (pesquisa-base, integrativa, sistemática, escopo) | Apenas 2 (pesquisa-base vs revisão-literatura) | Cada subtipo tem parâmetros qualitativamente diferentes (tipo de triagem, documentação, avaliação de qualidade), baseados em literatura metodológica estabelecida. |
| T3 | 2026-02-18 | Parâmetros quantitativos como defaults configuráveis, não prescrições | Valores fixos por modalidade | A literatura metodológica NÃO prescreve números mínimos de referências ou limites de resultados. Valores são orientações, ajustáveis via scope.yaml. |
| T4 | 2026-02-18 | Reference como dataclass com preenchimento progressivo | Múltiplas classes por etapa | Uma única dataclass simplifica serialização e passagem entre etapas. Campos opcionais (`None`) representam "ainda não processado". |
| T5 | 2026-02-18 | JSON intermediário entre etapas (refs-raw, refs-dedup, etc.) | Banco de dados | JSON é legível, versionável no Git, e permite retomar o pipeline de qualquer etapa. Para o MVP, suficiente. |
| T6 | 2026-02-18 | Fuzzy matching com rapidfuzz (threshold 85%) | Correspondência exata apenas | DOI resolve ~80% das duplicatas; fuzzy pega variações de título (maiúsculas, pontuação, typos) nos ~20% restantes. |
| T7 | 2026-02-18 | PubMed + OpenAlex como APIs primárias | Todas as APIs desde o início | Cobrem >90% da literatura biomédica. Semantic Scholar e Europe PMC planejados para Fase 6. |
| T8 | 2026-02-18 | check_refs.py original preservado sem modificação | Refatorar in-place | O script original permanece funcional para auditoria ad hoc. As funções reutilizáveis foram extraídas (não copiadas) para o pipeline. |
| T9 | 2026-02-18 | CLI com argparse (subcomandos) | CLI mínima ou sem CLI | Subcomandos permitem execução parcial, debugging e integração com scripts. Mais flexível que um único `main()`. |
| T10 | 2026-02-18 | Keywords de relevância no scope.yaml (não hardcoded) | Keywords fixas no código | Cada projeto tem escopo diferente. O scope.yaml é o ponto único de configuração por projeto. |
| T11 | 2026-02-19 | Facts Registry como camada preventiva separada do verify-claims | Integrar no mesmo módulo | São responsabilidades diferentes (prevenção vs correção). Desacoplamento permite evolução independente. |
| T12 | 2026-02-19 | Extração em 2 camadas (Python automático + Claude interativo) | Extração totalmente manual | Automatizar ~30% (quantitativos) reduz trabalho; qualitativos requerem julgamento semântico. |
| T13 | 2026-02-19 | Validação cruzada independente de números (_cross_validate_value) | Confiar na extração do Claude | O mesmo LLM que pode hallucinar é quem extrai. Validação cruzada por regex é defesa mecânica contra fabricação. |
| T14 | 2026-02-19 | JSONL como formato intermediário (pending), JSON como registry | Tudo em JSON | JSONL permite append incremental (1 ref por vez) e processamento linha a linha com tratamento de erros. |


## Decisões Metodológicas

| # | Data | Decisão | Fonte | Justificativa |
|---|---|---|---|---|
| M1 | 2026-02-18 | Diferenciação qualitativa entre modalidades baseada em literatura | Grant & Booth (2009), Cochrane, PRISMA, JBI | Parâmetros como tipo de triagem (keywords vs LLM vs humana), documentação (search log vs protocolo) e avaliação de qualidade (nenhuma vs GRADE) são fundamentados em metodologia publicada. |
| M2 | 2026-02-18 | PRISMA obrigatório para sistemática, PRISMA-ScR para escopo | Page et al. (2021), Tricco et al. (2018) | Standards estabelecidos e amplamente adotados. |
| M3 | 2026-02-18 | Postura na dúvida varia por modalidade | Cochrane MECIR, Munn et al. (2018) | Pesquisa-base exclui; revisão-escopo inclui. Coerente com o propósito de cada modalidade. |
| M4 | 2026-02-18 | Avaliação de qualidade obrigatória apenas para sistemática | Cochrane Handbook, Peters et al. (2020) | Revisão de escopo não requer avaliação de qualidade (Peters). Pesquisa-base é intermediário, não produto final. |


## Fontes Autoritativas

1. Grant MJ, Booth A. A typology of reviews: an analysis of 14 review types and associated methodologies. *Health Info Libr J.* 2009;26(2):91-108.
2. Cochrane Handbook for Systematic Reviews of Interventions, Cap. 4 — MECIR standards.
3. Page MJ et al. The PRISMA 2020 statement: an updated guideline for reporting systematic reviews. *BMJ.* 2021;372:n71.
4. Tricco AC et al. PRISMA Extension for Scoping Reviews (PRISMA-ScR). *Ann Intern Med.* 2018;169(7):467-473.
5. Whittemore R, Knafl K. The integrative review: updated methodology. *J Adv Nurs.* 2005;52(5):546-553.
6. Arksey H, O'Malley L. Scoping studies: towards a methodological framework. *Int J Soc Res Methodol.* 2005;8(1):19-32.
7. Peters MDJ et al. Updated methodological guidance for the conduct of scoping reviews. *JBI Evid Synth.* 2020;18(10):2119-2126.
8. Munn Z et al. Systematic review or scoping review? Guidance for authors when choosing between a systematic or scoping review approach. *BMC Med Res Methodol.* 2018;18:143.
9. Levac D, Colquhoun H, O'Brien KK. Scoping studies: advancing the methodology. *Implement Sci.* 2010;5:69.
