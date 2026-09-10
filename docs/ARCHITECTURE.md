# Sua Barbearia — Arquitetura

## 1. Visão geral

```
┌─────────────────────────────────────────┐
│              FLUTTER (1 app)            │
│      Android  ·  iOS  ·  Flutter Web    │
│  Riverpod · GoRouter · Dio · Freezed    │
└────────────────────┬────────────────────┘
                     │ HTTPS / REST / JSON  (JWT Bearer)
┌────────────────────▼────────────────────┐
│         NGINX (proxy + static/media)    │
└────────────────────┬────────────────────┘
┌────────────────────▼────────────────────┐
│        DJANGO REST FRAMEWORK            │
│  Views → Serializers → Services → ORM   │
└───┬──────────────┬──────────────┬───────┘
    │              │              │
┌───▼────┐   ┌─────▼─────┐   ┌────▼──────┐
│Postgres│   │   Redis   │   │  Celery   │
│  16    │   │ cache/brk │   │ + Beat    │
└────────┘   └───────────┘   └───────────┘
```

**Camadas do backend** (regra: view fina, serviço gordo):

| Camada | Responsabilidade |
|---|---|
| `views.py` | HTTP, permissões, paginação, filtros |
| `serializers.py` | Validação de entrada e forma de saída |
| `services/` | Regra de negócio, transações, invariantes |
| `models.py` | Estrutura, constraints e propriedades derivadas |
| `tasks.py` | Processamento assíncrono (Celery) |
| `filters.py` | Filtros declarativos (django-filter) |

Nenhuma regra de negócio vive na view ou no Flutter. O Flutter é uma casca de
apresentação: **toda** permissão é reavaliada no backend.

## 2. Modelo de dados (ERD)

```
                            ┌──────────────┐
                            │     User     │  role: OWNER | BARBER | CLIENT
                            └──┬────────┬──┘
                    1:1 ┌──────┘        └──────┐ 1:1
                 ┌──────▼──────┐        ┌──────▼──────┐
                 │   Barber    │        │   Client    │
                 └──┬───┬───┬──┘        └──┬───┬───┬──┘
        M:N branches│   │   │M:N services  │   │   │
      ┌─────────────┘   │   └───────┐      │   │   └────────┐
┌─────▼──────┐   ┌──────▼──────┐  ┌─▼──────▼─┐ │  ┌─────────▼────────┐
│   Branch   │   │ WorkingHour │  │ Service  │ │  │  LoyaltyAccount  │
│            │   │  TimeOff    │  │ Category │ │  │  LoyaltyTransac. │
│ OpeningHour│   │ SpecialHour │  └────┬─────┘ │  └──────────────────┘
│ Holiday    │   └─────────────┘       │       │
└─────┬──────┘                         │       │
      │              ┌─────────────────┴───────┴─────┐
      └──────────────►         Appointment           │
                     │  status: PENDING→…→COMPLETED  │
                     └───┬──────────┬──────────┬─────┘
                         │          │          │
              ┌──────────▼──┐  ┌────▼─────┐ ┌──▼───────┐
              │   Payment   │  │Commission│ │  Review  │
              └──────┬──────┘  └────┬─────┘ └──────────┘
                     │              │
                ┌────▼──────────────▼────┐
                │      Transaction       │  INCOME / EXPENSE
                └────────────────────────┘

  Produtos/Estoque:  Product ──< StockItem >── Branch
                     Product ──< StockMovement (histórico imutável)
                     Sale ──< SaleItem >── Product
```

### Entidades e chaves

| App | Modelos |
|---|---|
| `core` | `AuditLog`, bases `BaseModel`/`ActivableModel` |
| `accounts` | `User` (custom), `PasswordResetToken` |
| `branches` | `Branch`, `OpeningHour`, `BranchHoliday` |
| `barbers` | `Barber`, `BarberService`, `WorkingHour`, `TimeOff`, `SpecialWorkingHour` |
| `clients` | `Client` |
| `services` | `ServiceCategory`, `Service` |
| `appointments` | `Appointment`, `AppointmentStatusHistory` |
| `payments` | `Payment` (agnóstico de gateway) |
| `finance` | `Transaction`, `Commission` |
| `products` | `ProductCategory`, `Product` |
| `inventory` | `StockItem`, `StockMovement`, `Sale`, `SaleItem` |
| `loyalty` | `LoyaltyAccount`, `LoyaltyTransaction`, `LoyaltyReward` |
| `plans` | `Plan`, `PlanService`, `Subscription`, `SubscriptionInvoice`, `SubscriptionUsage` |
| `reviews` | `Review` |
| `notifications` | `Notification`, `DeviceToken` |
| `reports` | (sem modelos — agregações) |

**Decisão de projeto:** o estoque é por filial (`StockItem`), não um campo em
`Product`. Um sistema multi-filial precisa saber o saldo de cada unidade;
`Product.total_stock` expõe o consolidado.

## 3. Identificadores

- PK interna: `BigAutoField` (performance de índice e joins).
- Identificador público: `uuid` (`UUIDField`, único, indexado) em todo modelo
  que trafega para o cliente. Evita enumeração de recursos.

## 4. Envelope de resposta

Sucesso:
```json
{ "success": true, "data": {}, "message": null, "errors": null }
```

Erro:
```json
{ "success": false, "data": null, "message": "…", "code": "BARBER_NOT_AVAILABLE", "errors": {} }
```

`code` é um identificador estável que o Flutter traduz para mensagem amigável
(`lib/core/errors/error_messages.dart`). Nunca expomos stack trace.

## 5. Regras de negócio críticas

### 5.1 Disponibilidade e agendamento
Um horário só é ofertado se **todas** as condições forem verdadeiras:

1. A filial está aberta naquele dia/hora (`OpeningHour`, respeitando `BranchHoliday`).
2. O barbeiro trabalha naquele dia/filial (`WorkingHour`, sobreposto por `SpecialWorkingHour`).
3. O horário não cai no intervalo de almoço.
4. Não existe `TimeOff` cobrindo o intervalo.
5. Não há `Appointment` com status bloqueante sobrepondo o intervalo.
6. O slot inteiro (início + duração do serviço) cabe antes do fim do expediente.
7. O horário é futuro e está dentro de `max_advance_booking_days`.
8. O barbeiro executa o serviço (`BarberService` ativo) e atende naquela filial.

### 5.2 Proteção contra race condition
- `UniqueConstraint(barber, date, start_time)` **condicional** aos status
  bloqueantes — garante no banco que dois clientes não peguem o mesmo slot.
- `transaction.atomic()` + `select_for_update()` sobre os agendamentos do
  barbeiro no dia, antes de revalidar a sobreposição.
- `IntegrityError` é convertido em `409 SLOT_ALREADY_TAKEN`.

### 5.3 Máquina de estados do atendimento
```
PENDING ──► CONFIRMED ──► ARRIVED ──► IN_PROGRESS ──► COMPLETED
   │            │            │
   └────────────┴────────────┴──► CANCELLED | NO_SHOW
```
Transições fora do mapa retornam `400 INVALID_STATUS_TRANSITION`.

### 5.4 Cancelamento
Cliente só cancela até `branch.cancellation_limit_hours` antes do início.
OWNER e BARBER cancelam a qualquer momento. Sempre gravamos quem cancelou
(`cancelled_by_role`), o usuário e o motivo, mais uma linha em
`AppointmentStatusHistory`.

### 5.5 Conclusão do atendimento (transação única)
Ao concluir: grava `Payment` → `Transaction` (INCOME/SERVICES) →
`Commission` (percentual do barbeiro) → pontos de fidelidade →
agregados do cliente (`total_visits`, `total_spent`, `last_visit_at`,
`favorite_service`). Tudo dentro de um único `transaction.atomic()`.

### 5.6 Estoque
Nenhuma escrita em `StockItem.quantity` sem `StockMovement` correspondente.
Venda com saldo insuficiente → `400 INSUFFICIENT_STOCK`. Constraint no banco
impede saldo negativo.

### 5.7 Fidelidade
`pontos = floor(valor_pago × branch.loyalty_points_per_currency_unit)`.
Resgate valida saldo com `select_for_update()` na conta.

### 5.8 Avaliação
Só para `Appointment` com status `COMPLETED`, só pelo cliente do atendimento,
uma única vez (`OneToOneField`). Salvar recalcula `Barber.rating`.

### 5.9 Planos mensais e assinaturas

**Uma assinatura viva por cliente**, garantido por `UniqueConstraint` sobre
`client` filtrada nos status `PENDING_PAYMENT`/`ACTIVE`/`PAST_DUE`. Trocar de
plano exige cancelar a atual, o que evita duas cotas somadas e cobrança dupla.

**Preço em snapshot.** `Subscription.price` é copiado do plano na assinatura:
reajustar o plano não altera o que quem já assinou paga.

**O benefício só vale com ciclo pago.** `grants_benefit` exige status `ACTIVE`
*e* `current_period_end >= hoje`. Um PIX emitido e não pago não dá corte de
graça.

**Cobrança por meio de pagamento.** O Mercado Pago não faz recorrência
automática por PIX. Daí os dois `billing_type`:

| Tipo | Como funciona |
|---|---|
| `CARD_RECURRING` | `preapproval` no provedor. O cartão é digitado no checkout **hospedado pelo Mercado Pago** (`init_point`); nem o app nem o backend recebem número, validade ou CVV. Cada ciclo chega por webhook `subscription_authorized_payment`. |
| `PIX_MONTHLY` | Uma cobrança nova por ciclo, com QR e copia e cola. A renovação é emitida pelo Celery com antecedência configurável. |

**Ciclo mensal** via `relativedelta`, não `timedelta(days=30)`: 31/01 fecha em
27/02, sem escorregar o dia de cobrança ao longo do ano.

**Idempotência.** `confirm_invoice` retorna cedo se a fatura já está paga — o
Mercado Pago reentrega webhooks, e lançar a receita duas vezes inflaria o
caixa. A unicidade `(subscription, period_start)` filtrada em
`PENDING`/`PAID` faz reemitir um PIX expirado **substituir** a cobrança em vez
de duplicar o mês.

**Efeito no atendimento** (dentro da mesma transação de `complete_appointment`):
cota disponível → desconto integral com `method: SUBSCRIPTION` e nenhuma
receita lançada (o dinheiro entrou na fatura); cota esgotada → aplica
`overage_discount_percentage` e **não** consome cota; desconto informado à mão
tem prioridade sobre o plano. `SubscriptionUsage.appointment` é único, então
finalizar duas vezes não consome dois usos.

**Comissão.** Um atendimento coberto não gera receita, mas o barbeiro
trabalhou. Com `Plan.pays_barber_commission` ligado (padrão), a comissão incide
sobre o preço do serviço. É decisão do proprietário, não do sistema.

## 6. Segurança

- JWT (SimpleJWT) com rotação e blacklist de refresh.
- Tokens no Flutter em `flutter_secure_storage` — nunca em SharedPreferences.
- Permissões por papel em toda view (`apps/core/permissions.py`) + filtro de
  queryset por dono do recurso (um barbeiro só enxerga a própria agenda).
- Throttling por escopo: login 10/min, reset de senha 5/h.
- CORS restrito, HSTS, cookies `Secure`/`HttpOnly`, `X-Frame-Options: DENY`.
- Segredos exclusivamente em variáveis de ambiente.
- Webhook de pagamento autenticado por HMAC-SHA256 (`x-signature`); sem
  segredo configurado o endpoint recusa tudo. O status **nunca** vem do
  corpo recebido: o `id` é usado para consultar o provedor.
- Dados de cartão nunca trafegam pela Sua Barbearia: o checkout é do provedor.
- `apps/payments/mercadopago.py` mascara chaves sensíveis antes de logar e
  jamais registra o `access_token`.
- `AuditLog` para operações sensíveis, com mascaramento de campos secretos.
- `X-Request-ID` em toda requisição, propagado ao log e à auditoria.

## 7. Performance

- `select_related` / `prefetch_related` em todos os endpoints de listagem.
- Índices compostos nos caminhos quentes (`branch+date+status`,
  `barber+date+start_time`, `client+-date`).
- Cache Redis para dashboards e slots disponíveis (TTL curto).
- Paginação obrigatória em toda lista.
- Notificações e lembretes fora do ciclo HTTP, via Celery.

## 8. Plano de fases

| Fase | Escopo | Status |
|---|---|---|
| 1 | Monorepo, Docker, Django, Postgres, Redis, Celery, Flutter | ✅ |
| 2 | User custom, autenticação, permissões, filiais | ✅ |
| 3 | Barbeiros, clientes, serviços, jornada de trabalho | ✅ |
| 4 | Agendamentos, disponibilidade, cancelamento, reagendamento | ✅ |
| 5 | Flutter: login, registro, rotas, estado de auth | ✅ |
| 6 | Flutter Owner: dashboard e cadastros | ✅ |
| 7 | Flutter Barber: dashboard, agenda, atendimento | ✅ |
| 8 | Flutter Client: home, booking, histórico, perfil | ✅ |
| 9 | Pagamentos, lançamentos, comissões | ✅ |
| 10 | Produtos, estoque, vendas | ✅ |
| 11 | Fidelidade | ✅ |
| 12 | Avaliações | ✅ |
| 13 | Notificações e lembretes (Celery) | ✅ |
| 14 | Relatórios e dashboards | ✅ |
| 15 | Testes backend e Flutter | ✅ |
| 16 | Produção: Nginx, HTTPS, env, CI/CD | ✅ |

### Verificação executada

| Verificação | Resultado |
|---|---|
| `manage.py check` | sem problemas |
| `makemigrations --check` | sem migrations pendentes |
| `ruff check` / `ruff format --check` | limpos |
| `pytest` | 161 testes passando |
| `manage.py spectacular` | schema gerado, 0 erros |
| `flutter analyze` | `No issues found!` |
| `flutter test` | 61 testes passando |
| `flutter build web --release` | build concluído |
| `scripts/smoke_test.py` | fluxo real ponta a ponta validado |

## 9. Pontos de extensão já previstos

A arquitetura não fecha portas para o roadmap descrito na especificação:

| Evolução | Como encaixa |
|---|---|
| Pagamento online (Mercado Pago, Stripe, Asaas, PIX) | `payments/gateways.py` define a interface `PaymentGateway`; basta implementar e registrar em `GATEWAYS`. O `Payment` já tem `provider`, `external_id` e `provider_payload`. |
| Push notification (Firebase) | `notifications/push.py` tem `PushProvider`; `DeviceToken` já armazena os tokens por plataforma. |
| WhatsApp e e-mail transacional | `NotificationChannel` já prevê os canais; o envio roda em Celery. |
| Cupons e promoções | `loyalty` já modela conta, movimentos e recompensas tipadas. |
| Outros gateways para assinatura | `Subscription`/`SubscriptionInvoice` guardam `provider`, `external_id` e `provider_payload`; trocar o Mercado Pago por outro provedor não exige migração destrutiva. |
| Multi-empresa / SaaS | Toda operação é ancorada em `Branch`; introduzir uma `Company` acima de `Branch` não exige reescrever regras. |
| Observabilidade (Sentry, Datadog, Prometheus) | `X-Request-ID` em toda requisição, logging por categoria e Sentry plugável em `settings/prod.py`. |
