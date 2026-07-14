"""Recomendação de periódicos-alvo para submissão (Qualis CAPES).

Subpacote independente do pipeline de busca de literatura: aqui a unidade
é o PERIÓDICO (ISSN), não o artigo. Todo dado volátil carrega proveniência
(<campo>_fonte, <campo>_verificado_em); dado ausente é NULL, nunca chute.

Uso: python -m tools journals <subcomando>  (ver journals/cli.py)
"""
