<?php
/**
 * web/src/Services/I18nService.php
 * Simple internationalization service.
 */

declare(strict_types=1);

namespace MailReview\Services;

class I18nService
{
    private string $lang;
    private array $translations = [
        'en' => [
            'dashboard' => 'Dashboard',
            'logout' => 'Log out',
            'welcome' => 'Welcome',
            'role_admin' => 'Admin',
            'role_coworker' => 'Coworker',
            'review_reports' => 'Review Reports',
            'admin_actions' => 'Admin actions',
            'view_reports' => 'View imported reports',
            'view_overview' => 'View decision overview',
            'reports_list' => 'Reports List',
            'back_dashboard' => 'Back to Dashboard',
            'report_id' => 'Report ID',
            'mailbox' => 'Mailbox',
            'imported_at' => 'Imported At',
            'actions' => 'Actions',
            'review' => 'Review',
            'date' => 'Date',
            'from' => 'From',
            'subject' => 'Subject',
            'size' => 'Size',
            'duplicates' => 'Duplicates',
            'decision' => 'Decision',
            'note' => 'Note',
            'save' => 'Save',
            'saved' => 'Saved',
            'decision_keep' => 'Keep',
            'decision_delete' => 'Delete',
            'decision_unsure' => 'Unsure',
            'decision_none' => '',
            'filter_all' => 'All Decisions',
            'filter_duplicates' => 'Duplicates Only',
            'search_placeholder' => 'Search subject/sender...',
            'apply_filters' => 'Apply',
            'admin_overview' => 'Admin Overview',
            'updated_by' => 'Updated By',
            'updated_at' => 'Updated At',
            'status' => 'Status',
            'total_emails' => 'Total Emails',
            'reviewed_count' => 'Reviewed',
            'progress' => 'Progress',
        ],
        'de' => [
            'dashboard' => 'Übersicht',
            'logout' => 'Abmelden',
            'welcome' => 'Willkommen',
            'role_admin' => 'Admin',
            'role_coworker' => 'Mitarbeiter',
            'review_reports' => 'Berichte überprüfen',
            'admin_actions' => 'Admin-Aktionen',
            'view_reports' => 'Importierte Berichte anzeigen',
            'view_overview' => 'Entscheidungsübersicht anzeigen',
            'reports_list' => 'Berichtsliste',
            'back_dashboard' => 'Zurück zur Übersicht',
            'report_id' => 'Berichts-ID',
            'mailbox' => 'Postfach',
            'imported_at' => 'Importiert am',
            'actions' => 'Aktionen',
            'review' => 'Überprüfen',
            'date' => 'Datum',
            'from' => 'Von',
            'subject' => 'Betreff',
            'size' => 'Größe',
            'duplicates' => 'Duplikate',
            'decision' => 'Entscheidung',
            'note' => 'Notiz',
            'save' => 'Speichern',
            'saved' => 'Gespeichert',
            'decision_keep' => 'Behalten',
            'decision_delete' => 'Löschen',
            'decision_unsure' => 'Unsicher',
            'decision_none' => '',
            'filter_all' => 'Alle Entscheidungen',
            'filter_duplicates' => 'Nur Duplikate',
            'search_placeholder' => 'Suche Betreff/Absender...',
            'apply_filters' => 'Anwenden',
            'admin_overview' => 'Admin-Übersicht',
            'updated_by' => 'Aktualisiert von',
            'updated_at' => 'Aktualisiert am',
            'status' => 'Status',
            'total_emails' => 'E-Mails gesamt',
            'reviewed_count' => 'Überprüft',
            'progress' => 'Fortschritt',
        ],
        'uk' => [
            'dashboard' => 'Панель керування',
            'logout' => 'Вийти',
            'welcome' => 'Ласкаво просимо',
            'role_admin' => 'Адмін',
            'role_coworker' => 'Колега',
            'review_reports' => 'Перегляд звітів',
            'admin_actions' => 'Дії адміністратора',
            'view_reports' => 'Перегляд імпортованих звітів',
            'view_overview' => 'Огляд рішень',
            'reports_list' => 'Список звітів',
            'back_dashboard' => 'Назад до панелі',
            'report_id' => 'ID звіту',
            'mailbox' => 'Скринька',
            'imported_at' => 'Імпортовано',
            'actions' => 'Дії',
            'review' => 'Перегляд',
            'date' => 'Дата',
            'from' => 'Від',
            'subject' => 'Тема',
            'size' => 'Розмір',
            'duplicates' => 'Дублікати',
            'decision' => 'Рішення',
            'note' => 'Примітка',
            'save' => 'Зберегти',
            'saved' => 'Збережено',
            'decision_keep' => 'Залишити',
            'decision_delete' => 'Видалити',
            'decision_unsure' => 'Не впевнений',
            'decision_none' => '',
            'filter_all' => 'Всі рішення',
            'filter_duplicates' => 'Тільки дублікати',
            'search_placeholder' => 'Пошук теми/відправника...',
            'apply_filters' => 'Застосувати',
            'admin_overview' => 'Огляд адміністратора',
            'updated_by' => 'Оновив',
            'updated_at' => 'Оновлено',
            'status' => 'Статус',
            'total_emails' => 'Всього листів',
            'reviewed_count' => 'Переглянуто',
            'progress' => 'Прогрес',
        ]
    ];

    public function __construct(string $lang = 'en')
    {
        $this->lang = in_array($lang, ['en', 'de', 'uk']) ? $lang : 'en';
    }

    public function getLang(): string
    {
        return $this->lang;
    }

    public function t(string $key): string
    {
        return $this->translations[$this->lang][$key] ?? $key;
    }
}
