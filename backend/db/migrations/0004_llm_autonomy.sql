-- Le LLM devient autonome : il peut proposer HALT et une sortie qui n'est ni un
-- stop ni un take-profit. Les deux valeurs sont ajoutées aux ENUMs existants ;
-- aucune table n'est modifiée.
--
-- `ALTER TYPE ... ADD VALUE` est autorisé dans une transaction PostgreSQL 12+ tant
-- que la valeur n'est pas utilisée dans la même transaction. Le coureur applique
-- chaque migration dans sa propre transaction, et celle-ci ne fait qu'ajouter les
-- labels.

ALTER TYPE signal_action ADD VALUE IF NOT EXISTS 'HALT';
ALTER TYPE order_purpose ADD VALUE IF NOT EXISTS 'LLM_EXIT';
