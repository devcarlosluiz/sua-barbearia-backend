"""Popula o banco com dados de demonstração da Sua Barbearia.

Uso (apenas em desenvolvimento):

    python manage.py seed_data
    python manage.py seed_data --flush   # limpa os dados antes de recriar

Cria: 1 proprietário, 3 filiais, 8 barbeiros, 20 clientes, 10 serviços,
50 agendamentos, 10 produtos com estoque, recompensas, avaliações e caixa.
"""

from __future__ import annotations

import random
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import User, UserRole
from apps.appointments.models import Appointment, AppointmentStatus, CancelledBy
from apps.barbers.models import Barber, BarberService, TimeOff, TimeOffType, WorkingHour
from apps.branches.models import Branch, BranchHoliday, OpeningHour
from apps.clients.models import Client
from apps.core.utils import combine_local
from apps.finance.models import Transaction, TransactionCategory
from apps.finance.services import create_appointment_commission, record_expense, record_income
from apps.inventory.models import StockItem, StockMovementType
from apps.inventory.services import register_movement
from apps.loyalty.models import LoyaltyReward, LoyaltyRewardType
from apps.loyalty.services import earn_points
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.products.models import Product, ProductCategory
from apps.reviews.models import Review
from apps.services.models import Service, ServiceCategory

SERVICES: list[dict[str, Any]] = [
    {"name": "Corte Masculino", "duration": 30, "price": "45.00", "category": "Cabelo"},
    {"name": "Corte Degradê", "duration": 45, "price": "55.00", "category": "Cabelo"},
    {"name": "Corte Infantil", "duration": 30, "price": "40.00", "category": "Cabelo"},
    {"name": "Barba Tradicional", "duration": 30, "price": "35.00", "category": "Barba"},
    {"name": "Barba Terapia", "duration": 45, "price": "55.00", "category": "Barba"},
    {"name": "Corte + Barba", "duration": 60, "price": "75.00", "category": "Combos"},
    {"name": "Platinado", "duration": 120, "price": "180.00", "category": "Coloração"},
    {"name": "Luzes", "duration": 90, "price": "140.00", "category": "Coloração"},
    {"name": "Sobrancelha", "duration": 15, "price": "20.00", "category": "Estética"},
    {"name": "Hidratação Capilar", "duration": 30, "price": "50.00", "category": "Estética"},
]

BRANCHES: list[dict[str, Any]] = [
    {
        "name": "Sua Barbearia Centro",
        "cnpj": "11222333000181",
        "address": "Rua XV de Novembro",
        "number": "1200",
        "district": "Centro",
        "city": "Curitiba",
        "state": "PR",
        "zip_code": "80020310",
        "phone": "4133334444",
        "whatsapp": "41999990001",
        "latitude": "-25.4295200",
        "longitude": "-49.2712800",
    },
    {
        "name": "Sua Barbearia Shopping",
        "cnpj": "11222333000262",
        "address": "Avenida Cândido de Abreu",
        "number": "500",
        "complement": "Loja 210",
        "district": "Centro Cívico",
        "city": "Curitiba",
        "state": "PR",
        "zip_code": "80530000",
        "phone": "4133335555",
        "whatsapp": "41999990002",
        "latitude": "-25.4165000",
        "longitude": "-49.2680000",
    },
    {
        "name": "Sua Barbearia Zona Sul",
        "cnpj": "11222333000343",
        "address": "Avenida Winston Churchill",
        "number": "2300",
        "district": "Capão Raso",
        "city": "Curitiba",
        "state": "PR",
        "zip_code": "81130000",
        "phone": "4133336666",
        "whatsapp": "41999990003",
        "latitude": "-25.5060000",
        "longitude": "-49.2960000",
    },
]

BARBER_NAMES = [
    ("João", "Ribeiro", "Jota"),
    ("Marcos", "Almeida", "Marcão"),
    ("Rafael", "Souza", "Rafa"),
    ("Diego", "Ferreira", ""),
    ("Bruno", "Cardoso", "Bruninho"),
    ("Lucas", "Martins", ""),
    ("Thiago", "Oliveira", "TH"),
    ("Vinícius", "Barbosa", "Vini"),
]

CLIENT_NAMES = [
    ("Carlos", "Mendes"),
    ("Pedro", "Nunes"),
    ("André", "Lima"),
    ("Felipe", "Rocha"),
    ("Gustavo", "Pereira"),
    ("Rodrigo", "Teixeira"),
    ("Fernando", "Castro"),
    ("Eduardo", "Moraes"),
    ("Leonardo", "Pinto"),
    ("Ricardo", "Gomes"),
    ("Matheus", "Freitas"),
    ("Gabriel", "Antunes"),
    ("Henrique", "Barros"),
    ("Daniel", "Correia"),
    ("Renato", "Machado"),
    ("Alexandre", "Duarte"),
    ("Otávio", "Nogueira"),
    ("Sérgio", "Vieira"),
    ("Márcio", "Batista"),
    ("Paulo", "Ramos"),
]

PRODUCTS = [
    ("Pomada Modeladora Matte", "POM-001", "18.00", "35.00", "Cabelo"),
    ("Pomada Efeito Molhado", "POM-002", "19.00", "38.00", "Cabelo"),
    ("Shampoo Anticaspa 300ml", "SHA-001", "16.00", "32.00", "Cabelo"),
    ("Shampoo para Barba 200ml", "SHA-002", "17.00", "34.00", "Barba"),
    ("Óleo para Barba 30ml", "OLE-001", "22.00", "45.00", "Barba"),
    ("Balm para Barba 60g", "BAL-001", "24.00", "48.00", "Barba"),
    ("Gel Fixador Forte", "GEL-001", "12.00", "25.00", "Cabelo"),
    ("Talco Pós-Barba", "TAL-001", "10.00", "22.00", "Barba"),
    ("Loção Pós-Barba 100ml", "LOC-001", "20.00", "42.00", "Barba"),
    ("Kit Barba Completo", "KIT-001", "70.00", "139.00", "Kits"),
]

EXPENSES = [
    (TransactionCategory.RENT, "Aluguel da unidade", "4500.00"),
    (TransactionCategory.ELECTRICITY, "Conta de energia", "780.00"),
    (TransactionCategory.WATER, "Conta de água", "210.00"),
    (TransactionCategory.INTERNET, "Internet e telefonia", "260.00"),
    (TransactionCategory.SUPPLIES, "Compra de insumos", "950.00"),
    (TransactionCategory.MARKETING, "Impulsionamento de anúncios", "600.00"),
]

REWARDS = [
    ("Desconto de R$ 20", LoyaltyRewardType.DISCOUNT_FIXED, 500, "20.00"),
    ("Desconto de R$ 50", LoyaltyRewardType.DISCOUNT_FIXED, 1100, "50.00"),
    ("10% de desconto", LoyaltyRewardType.DISCOUNT_PERCENT, 400, "10.00"),
]

REVIEW_COMMENTS = [
    "Atendimento impecável, corte exatamente como pedi.",
    "Ambiente ótimo e profissional muito atencioso.",
    "Melhor barbearia da região, sempre saio satisfeito.",
    "Bom atendimento, só demorou um pouco para começar.",
    "Corte perfeito e barba bem feita. Recomendo!",
    "Voltarei com certeza.",
]


class Command(BaseCommand):
    help = "Popula o banco com dados de demonstração da Sua Barbearia (somente desenvolvimento)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Remove os dados existentes antes de recriar.",
        )
        parser.add_argument(
            "--appointments",
            type=int,
            default=50,
            help="Quantidade de agendamentos a gerar (padrão: 50).",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG and not options["flush"]:
            self.stdout.write(
                self.style.WARNING(
                    "DEBUG=False detectado. O seed é destinado a ambientes de desenvolvimento."
                )
            )

        random.seed(42)
        self.password = settings.SEED_DEFAULT_PASSWORD
        if len(self.password) < 8:
            raise CommandError("SEED_DEFAULT_PASSWORD precisa ter ao menos 8 caracteres.")

        if options["flush"]:
            self._flush()

        self.stdout.write(self.style.MIGRATE_HEADING("Semeando dados da Sua Barbearia..."))

        owner = self._create_owner()
        branches = self._create_branches()
        _, services = self._create_services()
        barbers = self._create_barbers(branches, services)
        clients = self._create_clients(branches, barbers)
        self._create_products(branches, owner)
        self._create_rewards()
        appointments = self._create_appointments(
            branches, barbers, clients, services, owner, options["appointments"]
        )
        self._create_reviews(appointments)
        self._create_expenses(branches, owner)
        self._create_time_offs(barbers, owner)

        self._report(owner, branches, barbers, clients, services, appointments)

    # ------------------------------------------------------------------
    # Limpeza
    # ------------------------------------------------------------------
    def _flush(self) -> None:
        self.stdout.write("Removendo dados existentes...")
        from apps.core.models import AuditLog
        from apps.finance.models import Commission
        from apps.inventory.models import Sale, SaleItem, StockMovement
        from apps.loyalty.models import LoyaltyAccount, LoyaltyTransaction
        from apps.notifications.models import DeviceToken, Notification

        for model in (
            Review,
            LoyaltyTransaction,
            LoyaltyAccount,
            LoyaltyReward,
            Commission,
            Transaction,
            Payment,
            SaleItem,
            Sale,
            StockMovement,
            StockItem,
            Product,
            ProductCategory,
            Notification,
            DeviceToken,
            Appointment,
            TimeOff,
            WorkingHour,
            BarberService,
            Barber,
            Client,
            Service,
            ServiceCategory,
            BranchHoliday,
            OpeningHour,
            Branch,
            AuditLog,
        ):
            model.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()

    # ------------------------------------------------------------------
    # Entidades
    # ------------------------------------------------------------------
    def _create_owner(self) -> User:
        owner, created = User.objects.get_or_create(
            email="owner@suabarbearia.com",
            defaults={
                "first_name": "Carlos",
                "last_name": "Proprietário",
                "phone": "41999990000",
                "role": UserRole.OWNER,
                "is_staff": True,
                "is_verified": True,
            },
        )
        if created:
            owner.set_password(self.password)
            owner.save(update_fields=["password"])
        return owner

    def _create_branches(self) -> list[Branch]:
        branches: list[Branch] = []
        for data in BRANCHES:
            branch, created = Branch.objects.get_or_create(
                name=data["name"],
                defaults={**data, "slug": slugify(data["name"])},
            )
            branches.append(branch)
            if not created:
                continue

            for weekday in range(0, 6):  # segunda a sábado
                OpeningHour.objects.create(
                    branch=branch,
                    weekday=weekday,
                    opens_at=time(9, 0),
                    closes_at=time(20, 0) if weekday < 5 else time(18, 0),
                )
            OpeningHour.objects.create(
                branch=branch,
                weekday=6,
                opens_at=time(9, 0),
                closes_at=time(13, 0),
                is_closed=True,
            )
        return branches

    def _create_services(self) -> tuple[dict[str, ServiceCategory], list[Service]]:
        categories: dict[str, ServiceCategory] = {}
        services: list[Service] = []

        for index, name in enumerate(["Cabelo", "Barba", "Combos", "Coloração", "Estética"]):
            category, _ = ServiceCategory.objects.get_or_create(
                name=name, defaults={"slug": slugify(name), "display_order": index}
            )
            categories[name] = category

        for index, data in enumerate(SERVICES):
            service, _ = Service.objects.get_or_create(
                name=data["name"],
                defaults={
                    "slug": slugify(data["name"]),
                    "duration_minutes": data["duration"],
                    "price": Decimal(data["price"]),
                    "category": categories[data["category"]],
                    "display_order": index,
                    "description": f"{data['name']} realizado por profissionais da Sua Barbearia.",
                },
            )
            services.append(service)
        return categories, services

    def _create_barbers(self, branches: list[Branch], services: list[Service]) -> list[Barber]:
        barbers: list[Barber] = []
        for index, (first, last, nickname) in enumerate(BARBER_NAMES):
            email = "barber@suabarbearia.com" if index == 0 else f"barbeiro{index + 1}@suabarbearia.com"
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "phone": f"4198888{index:04d}",
                    "role": UserRole.BARBER,
                    "is_verified": True,
                },
            )
            if created:
                user.set_password(self.password)
                user.save(update_fields=["password"])

            barber, barber_created = Barber.objects.get_or_create(
                user=user,
                defaults={
                    "nickname": nickname,
                    "bio": f"{first} atende na Sua Barbearia com foco em cortes masculinos modernos.",
                    "specialties": random.sample(
                        ["Degradê", "Navalhado", "Barba terapia", "Platinado", "Freestyle"], k=2
                    ),
                    "commission_percentage": Decimal(random.choice(["35.00", "40.00", "45.00"])),
                    "hired_at": date.today() - timedelta(days=random.randint(90, 900)),
                },
            )
            barbers.append(barber)
            if not barber_created:
                continue

            # Cada barbeiro atende na filial principal; alguns em duas.
            main_branch = branches[index % len(branches)]
            barber_branches = [main_branch]
            if index % 3 == 0 and len(branches) > 1:
                barber_branches.append(branches[(index + 1) % len(branches)])
            barber.branches.set(barber_branches)

            for service in random.sample(services, k=random.randint(5, len(services))):
                BarberService.objects.get_or_create(barber=barber, service=service)

            for branch in barber_branches:
                for weekday in range(0, 6):
                    WorkingHour.objects.get_or_create(
                        barber=barber,
                        branch=branch,
                        weekday=weekday,
                        defaults={
                            "starts_at": time(9, 0),
                            "ends_at": time(19, 0) if weekday < 5 else time(17, 0),
                            "break_starts_at": time(12, 0),
                            "break_ends_at": time(13, 0),
                        },
                    )
        return barbers

    def _create_clients(self, branches: list[Branch], barbers: list[Barber]) -> list[Client]:
        clients: list[Client] = []
        for index, (first, last) in enumerate(CLIENT_NAMES):
            email = "client@suabarbearia.com" if index == 0 else f"cliente{index + 1}@suabarbearia.com"
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "phone": f"4197777{index:04d}",
                    "role": UserRole.CLIENT,
                    "is_verified": True,
                },
            )
            if created:
                user.set_password(self.password)
                user.save(update_fields=["password"])

            client, _ = Client.objects.get_or_create(
                user=user,
                defaults={
                    "birth_date": date(
                        random.randint(1975, 2005), random.randint(1, 12), random.randint(1, 28)
                    ),
                    "preferred_branch": random.choice(branches),
                    "preferred_barber": random.choice(barbers) if index % 2 == 0 else None,
                    "accepts_marketing": index % 4 != 0,
                },
            )
            clients.append(client)
        return clients

    def _create_products(self, branches: list[Branch], owner: User) -> list[Product]:
        products: list[Product] = []
        for name, sku, cost, price, category_name in PRODUCTS:
            category, _ = ProductCategory.objects.get_or_create(
                name=category_name, defaults={"slug": slugify(category_name)}
            )
            product, created = Product.objects.get_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "category": category,
                    "cost_price": Decimal(cost),
                    "sale_price": Decimal(price),
                    "commission_percentage": Decimal("10.00"),
                    "description": f"{name} - linha profissional Sua Barbearia.",
                },
            )
            products.append(product)
            if not created:
                continue

            for branch in branches:
                StockItem.objects.get_or_create(
                    product=product, branch=branch, defaults={"minimum_stock": 5}
                )
                register_movement(
                    product_id=product.pk,
                    branch_id=branch.pk,
                    movement_type=StockMovementType.ENTRY,
                    quantity=random.randint(8, 40),
                    unit_cost=product.cost_price,
                    reason="Carga inicial de estoque",
                    created_by=owner,
                )
        return products

    def _create_rewards(self) -> None:
        for name, reward_type, points, value in REWARDS:
            LoyaltyReward.objects.get_or_create(
                name=name,
                defaults={
                    "type": reward_type,
                    "points_cost": points,
                    "discount_value": Decimal(value),
                    "description": f"Troque {points} pontos por {name.lower()}.",
                },
            )

    def _create_appointments(
        self,
        branches: list[Branch],
        barbers: list[Barber],
        clients: list[Client],
        services: list[Service],
        owner: User,
        quantity: int,
    ) -> list[Appointment]:
        if Appointment.objects.exists():
            self.stdout.write("Agendamentos já existem; pulando geração.")
            return list(Appointment.objects.all())

        today = timezone.localdate()
        appointments: list[Appointment] = []
        used_slots: set[tuple[int, date, time]] = set()

        for _ in range(quantity):
            barber = random.choice(barbers)
            branch = random.choice(list(barber.branches.all()))
            barber_service = random.choice(list(barber.barber_services.all()))
            service = barber_service.service
            client = random.choice(clients)

            offset = random.randint(-30, 14)
            day = today + timedelta(days=offset)
            if day.weekday() == 6:  # domingo: fechado
                day -= timedelta(days=1)

            start_hour = random.choice([9, 10, 11, 14, 15, 16, 17])
            start_minute = random.choice([0, 30])
            start = time(start_hour, start_minute)

            key = (barber.pk, day, start)
            if key in used_slots:
                continue
            used_slots.add(key)

            end_minutes = start_hour * 60 + start_minute + service.duration_minutes
            end = time(end_minutes // 60 % 24, end_minutes % 60)

            if day < today:
                status = random.choices(
                    [
                        AppointmentStatus.COMPLETED,
                        AppointmentStatus.CANCELLED,
                        AppointmentStatus.NO_SHOW,
                    ],
                    weights=[80, 14, 6],
                )[0]
            elif day == today:
                status = random.choice([AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED])
            else:
                status = random.choice([AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED])

            appointment = Appointment.objects.create(
                client=client,
                barber=barber,
                branch=branch,
                service=service,
                date=day,
                start_time=start,
                end_time=end,
                price=barber_service.price,
                status=status,
                notes="",
                created_by=owner,
                confirmed_at=(timezone.now() if status != AppointmentStatus.PENDING else None),
            )

            if status == AppointmentStatus.COMPLETED:
                self._settle_completed(appointment, owner)
            elif status == AppointmentStatus.CANCELLED:
                appointment.cancelled_at = timezone.now()
                appointment.cancelled_by_role = CancelledBy.CLIENT
                appointment.cancellation_reason = "Imprevisto do cliente."
                appointment.save(
                    update_fields=["cancelled_at", "cancelled_by_role", "cancellation_reason"]
                )

            appointments.append(appointment)
        return appointments

    def _settle_completed(self, appointment: Appointment, owner: User) -> None:
        """Gera pagamento, caixa, comissão, pontos e agregados de um atendimento."""
        completed_at = combine_local(appointment.date, appointment.end_time)
        appointment.completed_at = completed_at
        appointment.started_at = appointment.start_datetime
        appointment.arrived_at = appointment.start_datetime
        appointment.save(update_fields=["completed_at", "started_at", "arrived_at"])

        method = random.choice(
            [
                PaymentMethod.PIX,
                PaymentMethod.CREDIT_CARD,
                PaymentMethod.DEBIT_CARD,
                PaymentMethod.CASH,
            ]
        )
        payment = Payment.objects.create(
            branch=appointment.branch,
            appointment=appointment,
            client=appointment.client,
            amount=appointment.price,
            method=method,
            status=PaymentStatus.PAID,
            paid_at=completed_at,
            created_by=owner,
        )
        record_income(
            branch=appointment.branch,
            amount=appointment.price,
            description=f"{appointment.service.name} - {appointment.client.full_name}",
            payment=payment,
            appointment=appointment,
            barber=appointment.barber,
            date=appointment.date,
            created_by=owner,
        )
        create_appointment_commission(appointment, appointment.price)
        earn_points(
            client=appointment.client,
            branch=appointment.branch,
            amount=appointment.price,
            description=f"Atendimento: {appointment.service.name}",
            appointment=appointment,
            created_by=owner,
        )

        Client.objects.filter(pk=appointment.client_id).update(
            total_visits=F("total_visits") + 1,
            total_spent=F("total_spent") + appointment.price,
            last_visit_at=completed_at,
            favorite_service=appointment.service,
        )

    def _create_reviews(self, appointments: list[Appointment]) -> None:
        completed = [a for a in appointments if a.status == AppointmentStatus.COMPLETED]
        for appointment in random.sample(completed, k=min(len(completed), 25)):
            if Review.objects.filter(appointment=appointment).exists():
                continue
            Review.objects.create(
                appointment=appointment,
                client=appointment.client,
                barber=appointment.barber,
                branch=appointment.branch,
                rating=random.choices([5, 4, 3], weights=[70, 25, 5])[0],
                comment=random.choice(REVIEW_COMMENTS),
            )

    def _create_expenses(self, branches: list[Branch], owner: User) -> None:
        if Transaction.objects.filter(type="EXPENSE").exists():
            return
        today = timezone.localdate()
        for branch in branches:
            for month_offset in range(0, 2):
                reference = (today.replace(day=1) - timedelta(days=month_offset * 30)).replace(
                    day=5
                )
                for category, description, amount in EXPENSES:
                    record_expense(
                        branch=branch,
                        amount=Decimal(amount),
                        description=description,
                        category=category,
                        date=reference,
                        created_by=owner,
                    )

    def _create_time_offs(self, barbers: list[Barber], owner: User) -> None:
        if TimeOff.objects.exists():
            return
        now = timezone.now()
        for barber in random.sample(barbers, k=min(3, len(barbers))):
            start = now + timedelta(days=random.randint(3, 20))
            TimeOff.objects.create(
                barber=barber,
                type=TimeOffType.VACATION,
                starts_at=start.replace(hour=0, minute=0, second=0, microsecond=0),
                ends_at=(start + timedelta(days=5)).replace(
                    hour=23, minute=59, second=0, microsecond=0
                ),
                reason="Férias programadas",
                created_by=owner,
            )

    # ------------------------------------------------------------------
    # Relatório final
    # ------------------------------------------------------------------
    def _report(
        self,
        owner: User,
        branches: list[Branch],
        barbers: list[Barber],
        clients: list[Client],
        services: list[Service],
        appointments: list[Appointment],
    ) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Seed concluído com sucesso!"))
        self.stdout.write("")
        self.stdout.write(f"  Filiais ............ {len(branches)}")
        self.stdout.write(f"  Barbeiros .......... {len(barbers)}")
        self.stdout.write(f"  Clientes ........... {len(clients)}")
        self.stdout.write(f"  Serviços ........... {len(services)}")
        self.stdout.write(f"  Produtos ........... {Product.objects.count()}")
        self.stdout.write(f"  Agendamentos ....... {len(appointments)}")
        self.stdout.write(f"  Pagamentos ......... {Payment.objects.count()}")
        self.stdout.write(f"  Lançamentos ........ {Transaction.objects.count()}")
        self.stdout.write(f"  Avaliações ......... {Review.objects.count()}")
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Usuários de teste:"))
        self.stdout.write("  OWNER  : owner@suabarbearia.com")
        self.stdout.write("  BARBER : barber@suabarbearia.com")
        self.stdout.write("  CLIENT : client@suabarbearia.com")
        self.stdout.write(
            "  Senha  : definida em SEED_DEFAULT_PASSWORD (.env) — somente desenvolvimento"
        )
