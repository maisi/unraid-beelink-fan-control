<?php
// Unraid's PHP prepend enforces its session/CSRF policy for form POSTs.
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$request = ['action' => 'status'];
if ($method === 'GET' && ($_GET['action'] ?? '') === 'diagnostics') $request['action'] = 'diagnostics';
if ($method === 'POST') {
    $action = $_POST['action'] ?? '';
    if (!in_array($action, ['save', 'auto', 'test_start', 'test_stop'], true)) {
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => 'Unbekannte Aktion.']);
        exit;
    }
    $request['action'] = $action;
    if ($action === 'save') {
        $payload = $_POST['config'] ?? '';
        if (!is_string($payload) || strlen($payload) > 16384) {
            http_response_code(400);
            echo json_encode(['ok' => false, 'error' => 'Ungültige Konfiguration.']);
            exit;
        }
        $request['config'] = json_decode($payload, true);
    }
    if ($action === 'test_start') {
        $payload = $_POST['test'] ?? '';
        if (!is_string($payload) || strlen($payload) > 1024) {
            http_response_code(400);
            echo json_encode(['ok' => false, 'error' => 'Ungültiger Test.']);
            exit;
        }
        $request['test'] = json_decode($payload, true);
    }
} elseif ($method !== 'GET') {
    http_response_code(405);
    header('Allow: GET, POST');
    echo json_encode(['ok' => false, 'error' => 'Methode nicht erlaubt.']);
    exit;
}
$process = proc_open(
    ['/usr/bin/python3', __DIR__ . '/control.py', 'api'],
    [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
    $pipes
);
if (!is_resource($process)) {
    http_response_code(503);
    echo json_encode(['ok' => false, 'error' => 'Lüfterdienst ist nicht erreichbar.']);
    exit;
}
fwrite($pipes[0], json_encode($request));
fclose($pipes[0]);
stream_set_timeout($pipes[1], 8);
$output = stream_get_contents($pipes[1], 2097152);
fclose($pipes[1]);
fclose($pipes[2]);
$exit = proc_close($process);
$result = json_decode($output, true);
if (!is_array($result) || !array_key_exists('ok', $result)) {
    http_response_code(503);
    echo json_encode(['ok' => false, 'error' => 'Lüfterdienst hat keine gültige Antwort geliefert.']);
    exit;
}
if (!$result['ok'] || $exit !== 0) http_response_code(400);
echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
