# API Sua Barbearia — referência

Base: `/api/v1/`. Documentação interativa em `/api/docs/` (Swagger) e
`/api/redoc/`; schema OpenAPI em `/api/schema/`.

## Autenticação

JWT (SimpleJWT) com rotação e blacklist do refresh.

```http
Authorization: Bearer <access_token>
```

`POST /api/v1/auth/login/`

```json
{ "email": "owner@suabarbearia.com", "password": "SuaBarbearia@2026" }
```

```json
{
  "success": true,
  "data": {
    "access": "eyJhbGciOi...",
    "refresh": "eyJhbGciOi...",
    "user": {
      "id": 1,
      "uuid": "85123e1b-...",
      "name": "Carlos Proprietário",
      "email": "owner@suabarbearia.com",
      "role": "OWNER"
    }
  },
  "message": null,
  "errors": null
}
```

Quando o access expira, o app chama `POST /api/v1/auth/refresh/` com o refresh
token; se esse também falhar, a sessão é encerrada.

### Entrar com o Google

O mesmo endpoint cadastra e faz login: o app abre o Sign in with Google e envia
o `id_token` recebido. Não há senha nem data de nascimento no caminho — quem
entra pela primeira vez já sai autenticado, com papel `CLIENT`.

`POST /api/v1/auth/google/`

```json
{ "id_token": "eyJhbGciOiJSUzI1NiIs...", "preferred_branch_id": 1 }
```

A resposta é a mesma do login, com um `created` a mais — `true` quando a conta
acabou de ser criada (HTTP 201) e `false` quando era um login (HTTP 200):

```json
{
  "success": true,
  "data": {
    "access": "eyJhbGciOi...",
    "refresh": "eyJhbGciOi...",
    "created": true,
    "user": {
      "id": 42,
      "name": "Joana Souza",
      "email": "joana@gmail.com",
      "role": "CLIENT",
      "has_google_account": true,
      "has_password": false
    }
  },
  "message": null,
  "errors": null
}
```

Detalhes que valem para o app:

- `preferred_branch_id` é opcional e só é gravado no primeiro acesso.
- Se já existir uma conta com aquele e-mail, ela é **vinculada** à conta Google
  em vez de recusada — o Google já provou que o e-mail é da pessoa. A senha
  antiga continua valendo, e os dois caminhos de login passam a funcionar.
- `has_password: false` indica conta que só entra pelo Google. É o sinal para a
  tela de conta oferecer "criar senha" (via `forgot-password`) em vez de
  "alterar senha", que exige a senha atual.
- O vínculo é pelo `sub` do Google, não pelo e-mail: se a pessoa trocar o
  endereço no Google, continua entrando na mesma conta.

Erros específicos:

| HTTP | `code` | Quando |
| --- | --- | --- |
| 401 | `INVALID_GOOGLE_TOKEN` | Token expirado, adulterado, emitido para outro app ou com e-mail não verificado |
| 403 | `ACCOUNT_INACTIVE` | A conta existe, mas foi desativada pela barbearia |
| 503 | `GOOGLE_AUTH_NOT_CONFIGURED` | `GOOGLE_OAUTH_CLIENT_IDS` vazio no servidor |

No backend, configure `GOOGLE_OAUTH_CLIENT_IDS` com **todos** os client IDs da
credencial OAuth (Android, iOS e Web). O token só é aceito se o `aud` dele
estiver na lista — sem isso, um token emitido para outro aplicativo Google
serviria para entrar aqui.

## Envelope de resposta

Toda resposta segue o mesmo formato.

**Sucesso**
```json
{ "success": true, "data": { }, "message": null, "errors": null }
```

**Erro**
```json
{
  "success": false,
  "data": null,
  "message": "Este horário não está mais disponível. Escolha outro horário.",
  "code": "SLOT_NOT_AVAILABLE",
  "errors": {}
}
```

O `code` é estável e o frontend o traduz em
`frontend/lib/core/errors/error_messages.dart`.

## Paginação, filtros, busca e ordenação

Toda listagem é paginada:

```
GET /api/v1/clients/?page=2&page_size=20&search=carlos&ordering=-total_spent
```

```json
{
  "count": 123,
  "total_pages": 7,
  "current_page": 2,
  "page_size": 20,
  "next": "http://.../clients/?page=3",
  "previous": "http://.../clients/?page=1",
  "results": []
}
```

---

## Endpoints

Legenda de acesso: **O** = OWNER · **B** = BARBER · **C** = CLIENT · **–** = público.

### Autenticação e usuários

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| POST | `/auth/login/` | – | Login (retorna access, refresh e usuário) |
| POST | `/auth/google/` | – | Entrar com o Google (cadastra CLIENT na 1ª vez) |
| POST | `/auth/refresh/` | – | Renova o access token |
| POST | `/auth/logout/` | O B C | Invalida o refresh token |
| POST | `/auth/register/` | – | Cadastro público (sempre cria CLIENT) |
| POST | `/auth/forgot-password/` | – | Envia link de redefinição (sempre 202) |
| POST | `/auth/reset-password/` | – | Redefine a senha com o token |
| POST | `/auth/change-password/` | O B C | Troca a senha do usuário logado |
| GET/PATCH | `/auth/me/` | O B C | Usuário logado + perfil do papel |
| GET/POST/PATCH/DELETE | `/users/` | O | Gestão de usuários |

### Filiais

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/branches/public/` | – | Filiais ativas (usado no cadastro) |
| GET | `/branches/` | O B C | Lista filiais |
| POST/PATCH/DELETE | `/branches/` | O | Gestão de filiais |
| GET | `/branches/{id}/opening-hours/` | O B C | Horários de funcionamento |
| GET/POST | `/branches/{id}/holidays/` | O B C / O | Feriados e fechamentos |

### Serviços

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/services/` | O B C | Lista serviços (`?branch=`, `?barber=`) |
| POST/PATCH/DELETE | `/services/` | O | Gestão do catálogo |
| GET | `/service-categories/` | O B C | Categorias |

### Barbeiros e agenda de trabalho

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/barbers/` | O B C | Lista (`?branch=`, `?service=`) |
| POST/PATCH/DELETE | `/barbers/` | O | Gestão de barbeiros |
| GET | `/barbers/{id}/services/` | O B C | Serviços que o barbeiro realiza |
| GET/POST/PATCH/DELETE | `/working-hours/` | O (B lê a própria) | Jornada semanal |
| GET/POST/PATCH/DELETE | `/special-hours/` | O | Horário especial por data |
| GET/POST/DELETE | `/time-offs/` | O B | Férias, folgas e bloqueios |

### Clientes

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/clients/` | O B | Lista (B vê só quem já atendeu) |
| POST | `/clients/` | O B | Cadastro pelo balcão |
| GET/PATCH | `/clients/{id}/` | O B | Detalhe e edição |
| GET/PATCH | `/clients/me/` | C | Próprio perfil e preferências |

### Agendamentos

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/appointments/available-slots/` | O B C | **Horários livres** |
| GET | `/appointments/` | O B C | Lista (recortada pelo papel) |
| POST | `/appointments/` | O B C | Cria agendamento |
| GET | `/appointments/{id}/` | O B C | Detalhe com histórico |
| GET | `/appointments/agenda/` | O B | Agenda do dia |
| GET | `/appointments/upcoming/` | O B C | Próximos agendamentos |
| GET | `/appointments/history/` | O B C | Atendimentos concluídos |
| POST | `/appointments/{id}/cancel/` | O B C | Cancela |
| POST | `/appointments/{id}/reschedule/` | O B C | Reagenda |
| POST | `/appointments/{id}/status/` | O B | Muda o status |
| POST | `/appointments/{id}/arrive/` | O B | Cliente chegou |
| POST | `/appointments/{id}/start/` | O B | Inicia atendimento |
| POST | `/appointments/{id}/complete/` | O B | Finaliza + pagamento |
| POST | `/appointments/{id}/no-show/` | O B | Marca falta |
| DELETE | `/appointments/{id}/` | O C | Exclui o registro |

#### Cancelar × excluir

`POST /appointments/{id}/cancel/` mantém o registro com o motivo e quem
cancelou — é o caminho normal e continua disponível para os três papéis.

`DELETE /appointments/{id}/` apaga o registro de vez, junto com o histórico de
status. Serve para o lançamento errado no balcão, não para o dia a dia:

- o **proprietário** exclui qualquer agendamento não concluído, a qualquer
  momento;
- o **cliente** exclui apenas os próprios, e só em `PENDING`/`CONFIRMED` dentro
  do prazo de `cancellation_limit_hours` da filial — as mesmas condições do
  cancelamento. Fora disso: `400 CANCELLATION_DEADLINE_PASSED` ou
  `400 APPOINTMENT_NOT_DELETABLE_BY_CLIENT`. Sem esse limite, excluir seria a
  porta dos fundos do prazo: o cliente sumiria com o horário minutos antes e a
  barbearia não teria registro nem da desistência nem da falta;
- o **barbeiro** não exclui (`403 PERMISSION_DENIED`) — apagar o próprio
  atendimento faria sumir a evidência de uma falta;
- recusado para atendimentos concluídos
  (`400 APPOINTMENT_COMPLETED_CANNOT_DELETE`), que já geraram pagamento,
  receita, comissão e pontos de fidelidade;
- recusado quando existe qualquer pagamento vinculado
  (`400 APPOINTMENT_HAS_PAYMENT`) — `Payment.appointment` é `PROTECT`, então
  sem essa checagem o banco recusaria com um erro cru;
- devolve `204 No Content` e libera o horário na agenda.

#### Consulta de horários

```
GET /api/v1/appointments/available-slots/?branch_id=1&barber_id=2&service_id=3&date=2026-09-10
```

```json
{
  "success": true,
  "data": {
    "date": "2026-09-10",
    "branch_id": 1,
    "barber_id": 2,
    "service_id": 3,
    "duration_minutes": 30,
    "price": "45.00",
    "slots": ["09:00", "09:30", "10:30", "11:00"]
  }
}
```

#### Finalização do atendimento

```
POST /api/v1/appointments/12/complete/
{ "payment_method": "PIX", "amount": "45.00", "discount_amount": "0.00" }
```

Em uma única transação: conclui o agendamento, registra o `Payment`, lança a
receita no caixa, gera a comissão do barbeiro, credita os pontos de fidelidade
e atualiza os agregados do cliente.

### Pagamentos e financeiro

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET/POST | `/payments/` | O B | Pagamentos (B só dos próprios) |
| POST | `/payments/{id}/refund/` | O | Estorna |
| GET/POST/PATCH/DELETE | `/finance/transactions/` | O | Lançamentos |
| GET | `/finance/transactions/summary/` | O | Resumo de caixa |
| GET | `/finance/transactions/export/` | O | Exporta CSV |
| GET | `/finance/choices/` | O | Tipos e categorias |
| GET | `/finance/commissions/` | O B | Comissões (B só as próprias) |
| GET | `/finance/commissions/summary/` | O B | Total por status |
| POST | `/finance/commissions/pay/` | O | Paga comissões |

Filtros de período aceitos por `summary` e pelos dashboards:
`?period=today|7d|30d|this_month|last_month` ou
`?start_date=AAAA-MM-DD&end_date=AAAA-MM-DD`.

### Produtos, estoque e vendas

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/products/` | O B | Catálogo com estoque por filial |
| POST/PATCH/DELETE | `/products/` | O | Gestão de produtos |
| GET | `/product-categories/` | O B | Categorias |
| GET/PATCH | `/inventory/stock/` | O B | Saldos (PATCH ajusta o mínimo) |
| GET | `/inventory/stock/low-stock/` | O B | Abaixo do mínimo |
| GET/POST | `/inventory/movements/` | O B | Movimentações |
| GET/POST | `/sales/` | O B | Vendas de produtos |
| POST | `/sales/{id}/complete/` | O B | Conclui a venda |
| POST | `/sales/{id}/cancel/` | O | Cancela e devolve ao estoque |

Movimentações aceitas: `ENTRY`, `ADJUSTMENT` (informa o saldo final contado),
`LOSS` e `RETURN`. `SALE` é gerada apenas pelo fluxo de venda.

### Fidelidade

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/loyalty/accounts/me/` | C | Saldo + extrato |
| GET | `/loyalty/accounts/` | O | Contas dos clientes |
| GET | `/loyalty/transactions/` | O B C | Movimentos de pontos |
| GET | `/loyalty/rewards/` | O B C | Recompensas |
| POST/PATCH | `/loyalty/rewards/` | O | Gestão de recompensas |
| POST | `/loyalty/rewards/redeem/` | O B C | Resgata recompensa |
| POST | `/loyalty/rewards/adjust/` | O | Ajuste manual de pontos |

### Planos mensais e assinaturas

O cliente assina um plano e paga direto no app. O gateway é o **Mercado Pago**:
PIX gera QR a cada ciclo; cartão usa assinatura recorrente (`preapproval`) com
checkout hospedado pelo provedor — nem o app nem o backend recebem dados de
cartão.

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/plans/` | O B C | Lista planos (cliente vê só ativos e públicos) |
| GET | `/plans/{id}/` | O B C | Detalhe do plano |
| POST/PATCH | `/plans/` | O | Cria/edita plano e sua composição |
| DELETE | `/plans/{id}/` | O | Exclui (barrado se houver assinante) |
| GET | `/plans/{id}/subscribers/` | O | Assinantes do plano |
| GET | `/subscriptions/` | O B C | Assinaturas (cliente vê só a própria) |
| GET | `/subscriptions/me/` | C | Minha assinatura + cota do ciclo |
| POST | `/subscriptions/subscribe/` | C | Assina e devolve os dados de pagamento |
| POST | `/subscriptions/{id}/cancel/` | O C | Cancela (`immediate` só para OWNER) |
| POST | `/subscriptions/{id}/renew-pix/` | O C | Reemite o QR do ciclo em aberto |
| GET | `/subscription-invoices/` | O C | Faturas (cliente vê só as próprias) |
| POST | `/subscription-invoices/{id}/confirm/` | O | Confirma pagamento no caixa |
| POST | `/webhooks/mercado-pago/` | — | Notificação do provedor (HMAC) |

#### Composição do plano

`service_items` define o que o plano entrega. `monthly_quota: 0` é ilimitado.

```json
POST /api/v1/plans/
{
  "name": "Corte Mensal",
  "price": "129.90",
  "overage_discount_percentage": "20.00",
  "pays_barber_commission": true,
  "branches": [1],
  "service_items": [
    {"service": 3, "monthly_quota": 4},
    {"service": 4, "monthly_quota": 0}
  ]
}
```

#### Assinar

```json
POST /api/v1/subscriptions/subscribe/
{"plan": 1, "billing_type": "PIX_MONTHLY"}
```

A assinatura nasce em `PENDING_PAYMENT` e **não concede benefício** até o
pagamento ser confirmado. A resposta traz `open_invoice` com o que o cliente
precisa para pagar:

```json
{
  "status": "PENDING_PAYMENT",
  "grants_benefit": false,
  "open_invoice": {
    "method": "PIX",
    "amount": "99.90",
    "pix_qr_code": "00020126580014br.gov.bcb.pix...",
    "pix_qr_code_base64": "iVBORw0KG...",
    "expires_at": "2026-09-04T16:02:00Z"
  }
}
```

Com `billing_type: "CARD_RECURRING"` o `open_invoice` traz `checkout_url` em vez
do QR — o app abre essa URL e o cartão é informado no Mercado Pago.

#### Como o plano afeta o atendimento

Na finalização (`POST /appointments/{id}/complete/`), se o cliente tem
assinatura ativa e o serviço está no plano:

* **cota disponível** → desconto integral, `method: "SUBSCRIPTION"`, sem receita
  no caixa (a mensalidade já entrou) e um `SubscriptionUsage` registrado;
* **cota esgotada** → aplica `overage_discount_percentage`, cobra a diferença e
  **não** consome cota;
* **desconto informado à mão** → tem prioridade; o plano não é consumido.

A comissão do barbeiro incide sobre o preço do serviço quando
`pays_barber_commission` está ligado, mesmo o cliente não pagando no balcão.

### Avaliações

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/reviews/` | O B C | Lista (recortada pelo papel) |
| POST | `/reviews/` | C | Avalia atendimento concluído |
| GET | `/reviews/summary/` | O B C | Média e distribuição |
| GET | `/reviews/pending/` | C | Atendimentos sem avaliação |
| POST | `/reviews/{id}/reply/` | O | Responde a avaliação |

### Foto de perfil

Qualquer usuário autenticado pode enviar a própria foto — ela aparece no
cabeçalho, na barra lateral e nos cartões de agendamento.

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| PATCH | `/auth/me/` | O B C | Envia a foto (`multipart/form-data`, campo `avatar`) |
| DELETE | `/auth/me/avatar/` | O B C | Volta ao avatar de iniciais |

`DELETE /auth/me/` não existe de propósito: significaria excluir a conta.

| | |
|---|---|
| Formatos | PNG, JPEG, WEBP |
| Mínimo | 64 × 64 px |
| Máximo | 6000 × 6000 px, 5 MB |

No envio a foto é rotacionada conforme o EXIF (fotos de celular guardam a
orientação em metadados), recortada em quadrado a partir do centro, reduzida
para 512 × 512 e recomprimida em JPEG. Uma foto de 3000 × 2000 vira 512 × 512
com poucos KB. O arquivo recebe nome único a cada envio e o anterior é
apagado do disco.

### Identidade visual

A logo do sistema aparece no aplicativo inteiro e na tela de login. A leitura é
**pública** — a tela de login precisa dela antes de existir sessão; a escrita é
só do proprietário.

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/branding/` | — | Logo atual + formato recomendado |
| PUT | `/branding/` | O | Envia a logo (`multipart/form-data`, campo `logo`) |
| DELETE | `/branding/` | O | Volta à marca padrão |

```json
GET /api/v1/branding/
{
  "logo_url": "http://localhost:8000/media/branding/logo-56362dabbf44.png",
  "recommended_width": 600,
  "recommended_height": 160,
  "max_file_size_mb": 2,
  "updated_at": "2026-09-04T21:05:35Z"
}
```

`logo_url` nulo significa "sem logo enviada" — o app desenha a marca padrão.

**Formato aceito**

| | |
|---|---|
| Recomendado | **600 × 160 px**, PNG com fundo transparente |
| Formatos | PNG, JPEG, WEBP |
| Mínimo | 120 × 40 px |
| Máximo | 3000 × 3000 px, 2 MB |

A logo é desenhada com **altura fixa** (40 px na barra lateral, 52 px no login)
e largura proporcional, então formatos deitados encaixam melhor. No envio a
imagem é reduzida para no máximo 320 px de altura e convertida para PNG: ela é
baixada por todo cliente a cada abertura, e 320 px já cobre telas de altíssima
densidade. O arquivo recebe um nome único a cada envio — com nome fixo, o
navegador continuaria exibindo a logo anterior.

### Notificações

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/notifications/` | O B C | Lista (`?unread=true`) |
| GET | `/notifications/unread-count/` | O B C | Quantidade não lida |
| POST | `/notifications/mark-read/` | O B C | Marca como lidas |
| GET/POST/DELETE | `/device-tokens/` | O B C | Tokens de push |

### Dashboards

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/dashboard/owner/` | O | KPIs, gráficos e rankings |
| GET | `/dashboard/barber/` | B | Produção, comissão e agenda |
| GET | `/dashboard/client/` | C | Próximo horário, pontos, favoritos |

### Utilitários

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/health/` | – | Health check (banco e cache) |
| GET | `/api/schema/` | – | Schema OpenAPI |
| GET | `/api/docs/` | – | Swagger UI |
| GET | `/api/redoc/` | – | ReDoc |

---

## Códigos de erro

### Agendamento
| Código | Significado |
|---|---|
| `SLOT_NOT_AVAILABLE` | Horário indisponível na validação |
| `SLOT_ALREADY_TAKEN` | Corrida perdida: outro cliente reservou primeiro (409) |
| `BARBER_NOT_IN_BRANCH` | Barbeiro não atende naquela filial |
| `BARBER_DOES_NOT_PERFORM_SERVICE` | Barbeiro não faz o serviço |
| `BARBER_INACTIVE` / `BRANCH_INACTIVE` / `SERVICE_INACTIVE` | Recurso desativado |
| `DATE_IN_PAST` | Data no passado |
| `DATE_TOO_FAR` | Fora da antecedência máxima da filial |
| `CANCELLATION_DEADLINE_PASSED` | Prazo de cancelamento pelo app expirou |
| `INVALID_STATUS_TRANSITION` | Transição não permitida pela máquina de estados |
| `APPOINTMENT_ALREADY_FINISHED` | Já concluído/cancelado |
| `APPOINTMENT_NOT_STARTED` | Precisa iniciar antes de finalizar |
| `APPOINTMENT_COMPLETED_CANNOT_DELETE` | Concluído não é excluído — já gerou financeiro |
| `APPOINTMENT_NOT_DELETABLE_BY_CLIENT` | Cliente só exclui horário pendente/confirmado |
| `APPOINTMENT_HAS_PAYMENT` | Há pagamento vinculado; estorne antes |

### Financeiro e estoque
| Código | Significado |
|---|---|
| `DISCOUNT_GREATER_THAN_AMOUNT` | Desconto maior que o valor |
| `PAYMENT_NOT_REFUNDABLE` | Só pagamentos quitados são estornáveis |
| `NO_PENDING_COMMISSIONS` | Nenhuma comissão pendente |
| `INSUFFICIENT_STOCK` | Saldo insuficiente |
| `INVALID_QUANTITY` | Quantidade inválida |
| `SALE_WITHOUT_ITEMS` / `SALE_NOT_OPEN` | Venda inválida |

### Fidelidade e avaliações
| Código | Significado |
|---|---|
| `INSUFFICIENT_POINTS` | Saldo de pontos insuficiente |
| `REWARD_INACTIVE` | Recompensa desativada |
| `REVIEW_ALREADY_EXISTS` | Atendimento já avaliado |
| `APPOINTMENT_NOT_COMPLETED` | Só concluídos podem ser avaliados |

### Planos e assinaturas
| Código | HTTP | Significado |
|---|---|---|
| `PLAN_UNAVAILABLE` | 400 | Plano inativo ou oculto |
| `PLAN_HAS_SUBSCRIBERS` | 400 | Exclusão barrada; desative o plano |
| `PLAN_BRANCH_NOT_ALLOWED` | 400 | Plano não vale na filial escolhida |
| `SUBSCRIPTION_ALREADY_EXISTS` | 409 | Cliente já tem assinatura viva |
| `SUBSCRIPTION_NOT_ACTIVE` | 400 | Assinatura encerrada |
| `SUBSCRIPTION_IS_RECURRING` | 400 | Cobrança automática no cartão |
| `INVOICE_ALREADY_PAID` | 400 | Fatura já quitada |
| `INVOICE_NOT_PAYABLE` | 400 | Fatura cancelada ou estornada |
| `CLIENT_PROFILE_REQUIRED` | 403 | Só clientes assinam |
| `GATEWAY_NOT_CONFIGURED` | 503 | Falta credencial do Mercado Pago |
| `GATEWAY_ERROR` | 502 | Provedor indisponível ou recusou |

### Genéricos
| Código | HTTP | Significado |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Erros por campo em `errors` |
| `UNAUTHENTICATED` | 401 | Token ausente ou inválido |
| `PERMISSION_DENIED` | 403 | Papel sem acesso |
| `NOT_FOUND` | 404 | Registro inexistente |
| `CONFLICT` | 409 | Conflito de estado |
| `THROTTLED` | 429 | Excedeu o rate limit |
| `INTERNAL_ERROR` | 500 | Erro inesperado |

## Rate limiting

| Escopo | Limite padrão |
|---|---|
| Anônimo | 60/min |
| Autenticado | 1000/h |
| Login e registro | 10/min |
| Recuperação de senha | 5/h |

Ajustáveis por variável de ambiente (`THROTTLE_ANON`, `THROTTLE_USER`,
`THROTTLE_LOGIN`, `THROTTLE_PASSWORD_RESET`).

## Rastreabilidade

Toda requisição recebe um `X-Request-ID` (aceito também no request), propagado
para os logs e para o `AuditLog`.
