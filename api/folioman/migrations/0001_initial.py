# Generated by Django 3.1.2 on 2020-10-15 21:53

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AMC',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=128, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
                ('code', models.CharField(max_length=64, unique=True)),
            ],
        ),
        migrations.CreateModel(
            name='Folio',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.CharField(max_length=128, unique=True)),
                ('pan', models.CharField(blank=True, max_length=10, null=True)),
                ('kyc', models.BooleanField(default=False)),
                ('pan_kyc', models.BooleanField(default=False)),
                ('amc', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='folioman.amc')),
            ],
        ),
        migrations.CreateModel(
            name='FolioScheme',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('balance', models.DecimalField(decimal_places=3, max_digits=20)),
                ('balance_date', models.DateField()),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('modified', models.DateTimeField(auto_now=True)),
                ('folio', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='folioman.folio')),
            ],
        ),
        migrations.CreateModel(
            name='FundCategory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=32, unique=True)),
            ],
        ),
        migrations.CreateModel(
            name='Transaction',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('order_type', models.CharField(choices=[('Buy', 'Buy'), ('Redeem', 'Redeem')], max_length=8)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=20)),
                ('nav', models.DecimalField(decimal_places=4, max_digits=15)),
                ('units', models.DecimalField(decimal_places=3, max_digits=20)),
                ('scheme', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transactions', to='folioman.folioscheme')),
            ],
        ),
        migrations.CreateModel(
            name='Scheme',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=256, unique=True)),
                ('rta', models.CharField(blank=True, max_length=12, null=True)),
                ('plan', models.CharField(choices=[('REGULAR', 'Regular'), ('DIRECT', 'Direct')], default='REGULAR', max_length=8)),
                ('rta_code', models.CharField(max_length=32)),
                ('amc_code', models.CharField(db_index=True, max_length=32)),
                ('amfi_code', models.CharField(blank=True, db_index=True, max_length=8, null=True)),
                ('isin', models.CharField(db_index=True, max_length=16)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('modified', models.DateTimeField(auto_now=True)),
                ('amc', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='funds', to='folioman.amc')),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='funds', to='folioman.fundcategory')),
            ],
            options={
                'index_together': {('amc_id', 'rta_code'), ('rta', 'rta_code')},
            },
        ),
        migrations.CreateModel(
            name='Portfolio',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=32)),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('pan', models.CharField(blank=True, max_length=10, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='portfolios', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('user_id', 'name')},
            },
        ),
        migrations.AddField(
            model_name='folioscheme',
            name='scheme',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='folioman.scheme'),
        ),
        migrations.AddField(
            model_name='folio',
            name='portfolio',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='folios', to='folioman.portfolio'),
        ),
        migrations.CreateModel(
            name='NAVHistory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('nav', models.DecimalField(decimal_places=4, max_digits=15)),
                ('scheme', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='folioman.scheme')),
            ],
            options={
                'unique_together': {('scheme_id', 'date')},
            },
        ),
    ]