"""
Integracao com TCEES - validacao de documentos PDF.
Usa Selenium em modo headless e extrai os 8 indicadores do site
https://conformidadepdf.tcees.tc.br/
"""

from concurrent.futures import ThreadPoolExecutor
import os
import re
import shutil
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

TCEES_URL = 'https://conformidadepdf.tcees.tc.br/'


def _first_existing_path(candidates):
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def get_chrome_binary_path():
    """Resolve o binario do Chrome/Chromium sem download em runtime."""
    env_candidates = [
        os.getenv('CHROME_BIN'),
        os.getenv('GOOGLE_CHROME_BIN'),
        os.getenv('CHROMIUM_BIN'),
    ]
    which_candidates = [
        shutil.which('google-chrome'),
        shutil.which('google-chrome-stable'),
        shutil.which('chromium'),
        shutil.which('chromium-browser'),
        shutil.which('chrome'),
    ]
    common_paths = [
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
    ]
    return _first_existing_path(env_candidates + which_candidates + common_paths)


def get_chrome_driver_path():
    """Resolve o chromedriver local (PythonAnywhere/Linux/Windows)."""
    env_candidates = [
        os.getenv('CHROMEDRIVER_PATH'),
        os.getenv('SELENIUM_CHROMEDRIVER'),
        os.getenv('SELENIUM_DRIVER_PATH'),
    ]
    which_candidates = [
        shutil.which('chromedriver'),
    ]
    common_paths = [
        '/usr/bin/chromedriver',
        '/usr/local/bin/chromedriver',
        '/snap/bin/chromium.chromedriver',
    ]
    return _first_existing_path(env_candidates + which_candidates + common_paths)


def build_chrome_options():
    """Opcoes de headless estaveis para ambientes hospedados."""
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-software-rasterizer')
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--disable-infobars')
    chrome_options.add_argument('--disable-notifications')
    chrome_options.add_argument('--blink-settings=imagesEnabled=false')
    chrome_options.add_argument('--window-size=1366,768')
    chrome_options.add_argument('--lang=pt-BR')

    # Opcional: desativar proxy no Chrome headless.
    # Default = 0 para preservar o comportamento do ambiente hospedado.
    # Defina TCEES_DISABLE_CHROME_PROXY=1 se precisar forcar conexao direta.
    if os.getenv('TCEES_DISABLE_CHROME_PROXY', '0') == '1':
        chrome_options.add_argument('--no-proxy-server')
        chrome_options.add_argument('--proxy-server=direct://')
        chrome_options.add_argument('--proxy-bypass-list=*')

    chrome_options.page_load_strategy = 'eager'

    chrome_binary = get_chrome_binary_path()
    if chrome_binary:
        chrome_options.binary_location = chrome_binary

    return chrome_options


def _friendly_network_error(raw_error):
    text = str(raw_error or '')
    upper = text.upper()

    if 'ERR_TUNNEL_CONNECTION_FAILED' in upper:
        return (
            'TCEES_NETWORK_BLOCKED',
            'Servidor hospedado sem acesso ao site do TCEES (ERR_TUNNEL_CONNECTION_FAILED). '
            'No PythonAnywhere, isso costuma ocorrer por restricao de rede/proxy/allowlist.',
        )

    if 'ERR_NAME_NOT_RESOLVED' in upper:
        return (
            'TCEES_DNS_ERROR',
            'Falha de DNS ao resolver o dominio do TCEES no servidor hospedado.',
        )

    if 'ERR_CONNECTION_TIMED_OUT' in upper or 'TIMEOUT' in upper:
        return (
            'TCEES_TIMEOUT',
            'Tempo limite ao conectar no site do TCEES a partir do servidor hospedado.',
        )

    if 'ERR_CONNECTION_REFUSED' in upper:
        return (
            'TCEES_CONNECTION_REFUSED',
            'Conexao recusada ao acessar o TCEES no servidor hospedado. '
            'Pode ser bloqueio de rede/firewall/allowlist.',
        )

    if 'ERR_CONNECTION_CLOSED' in upper:
        return (
            'TCEES_CONNECTION_CLOSED',
            'Conexao encerrada pelo destino/rede ao tentar acessar o TCEES.',
        )

    return None, text


def _status_from_cell_html(cell_html):
    html = (cell_html or '').lower()

    success_markers = (
        'fa-check',
        'text-success',
        'title="ok"',
    )
    failure_markers = (
        'fa-close',
        'fa-times',
        'text-danger',
        'nao assinado',
        'não assinado',
        'invalido',
        'inválido',
        'erro',
    )

    is_success = any(marker in html for marker in success_markers)
    is_failure = any(marker in html for marker in failure_markers)

    if is_failure and not is_success:
        return False
    if is_success and not is_failure:
        return True
    if is_success and is_failure:
        return False
    return None


def _statuses_signature(statuses):
    if not statuses:
        return ''
    return ''.join('1' if s is True else '0' if s is False else '?' for s in statuses)


def _extract_statuses_from_driver(driver):
    selectors = [
        '#validacoes-arquivo div.row.text-center div.d-inline-block',
        '#validacoes-arquivo div.d-inline-block',
        '#validacoes-body #validacoes-arquivo div.d-inline-block',
    ]

    best_candidate = None
    best_resolved = -1

    for selector in selectors:
        try:
            cells = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue

        if len(cells) < 8:
            continue

        statuses = []
        for cell in cells[:8]:
            try:
                cell_html = cell.get_attribute('innerHTML') or ''
            except Exception:
                cell_html = ''
            statuses.append(_status_from_cell_html(cell_html))

        resolved = sum(status is not None for status in statuses)
        if resolved > best_resolved:
            best_candidate = statuses
            best_resolved = resolved

        if resolved >= 6:
            return statuses

    return best_candidate


def _extract_statuses_from_html(page_html):
    if not page_html:
        return None

    start = page_html.find('id="validacoes-arquivo"')
    if start < 0:
        return None

    snippet = page_html[start:start + 30000]

    cells = re.findall(
        r'<div[^>]*class="[^"]*d-inline-block[^"]*"[^>]*>(.*?)</div>',
        snippet,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if len(cells) < 8:
        return None

    statuses = [_status_from_cell_html(cell_html) for cell_html in cells[:8]]
    return statuses


def _apply_statuses_to_results(results, statuses, page_text=''):
    if not statuses or len(statuses) < 8:
        return False

    results['extensao_valida'] = statuses[0] is True
    results['sem_senha'] = statuses[1] is True
    results['tamanho_arquivo_ok'] = statuses[2] is True
    results['tamanho_pagina_ok'] = statuses[3] is True

    results['assinado'] = statuses[4] is True
    results['numero_assinaturas'] = 1 if results['assinado'] else 0

    aut_integ = statuses[5]
    results['autenticidade_ok'] = aut_integ is True
    results['integridade_ok'] = aut_integ is True

    results['pesquisavel'] = statuses[6] is True

    if statuses[7] is True:
        results['resultado_final'] = 'VALIDADO'
    elif statuses[7] is False:
        results['resultado_final'] = 'NÃO VALIDADO'
    else:
        # Sem status claro na coluna final, inferir pelo conjunto de checks
        resultados_base = [
            results['extensao_valida'],
            results['sem_senha'],
            results['tamanho_arquivo_ok'],
            results['tamanho_pagina_ok'],
            results['assinado'],
            results['autenticidade_ok'],
            results['pesquisavel'],
        ]
        results['resultado_final'] = 'VALIDADO' if all(resultados_base) else 'NÃO VALIDADO'

    lower_text = (page_text or '').lower()
    if 'nao assinado' in lower_text or 'não assinado' in lower_text:
        if not results['assinado']:
            results['mensagem_erro'] = 'Arquivo não assinado'

    return True


def validate_pdf_with_tcees(pdf_path, quick_mode=False):
    """
    Valida PDF via TCEES e retorna os 8 checks de conformidade.
    """
    print(f"\n[🔐] Validando documento com TCEES: {os.path.basename(pdf_path)}")

    if not os.path.isfile(pdf_path):
        return {
            'nome_arquivo': os.path.basename(pdf_path),
            'resultado_final': 'ERRO',
            'pontuacao': 0,
            'erro': f'Arquivo não encontrado: {pdf_path}',
        }

    chrome_options = build_chrome_options()

    wait_page_load = 2 if quick_mode else 3
    wait_render = 2 if quick_mode else 4
    max_wait = 30 if quick_mode else 55
    poll_interval = 2

    driver = None

    try:
        print('   [🌐] Abrindo navegador headless...')
        start_time = time.time()

        chromedriver_path = get_chrome_driver_path()
        if chromedriver_path:
            service = Service(chromedriver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
        else:
            print('   [⚠️] chromedriver não encontrado via PATH/env; tentando Selenium local...')
            driver = webdriver.Chrome(options=chrome_options)

        driver.set_page_load_timeout(25)
        print(f"   [⚡] Navegador pronto em {time.time() - start_time:.1f}s")

        print(f'   [📡] Acessando {TCEES_URL}...')
        driver.get(TCEES_URL)
        time.sleep(wait_page_load)

        print('   [📤] Fazendo upload do documento...')
        file_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="file"]'))
        )
        file_input.send_keys(os.path.abspath(pdf_path))
        print('   [✅] Arquivo enviado!')

        print('   [⏳] Aguardando processamento do TCEES...')
        waited = 0
        stable_count = 0
        last_signature = ''
        parsed_statuses = None

        while waited < max_wait:
            time.sleep(poll_interval)
            waited += poll_interval

            statuses = _extract_statuses_from_driver(driver)
            if statuses and len(statuses) >= 8:
                resolved = sum(status is not None for status in statuses)
                signature = _statuses_signature(statuses)

                if resolved >= 6:
                    if signature == last_signature:
                        stable_count += 1
                    else:
                        stable_count = 0
                        last_signature = signature

                    parsed_statuses = statuses
                    if stable_count >= 1:
                        print(f'   [✅] Resultado estável detectado em {waited}s')
                        break
                else:
                    print(f'   [⏳] Resultado parcial ({resolved}/8) em {waited}s...')
            else:
                print(f'   [⏳] Processando... ({waited}s)')

        if not parsed_statuses:
            print('   [⚠️] Sem status estável no tempo limite; aguardando render final...')
            time.sleep(wait_render)

        results = {
            'nome_arquivo': os.path.basename(pdf_path),
            'tamanho_bytes': os.path.getsize(pdf_path),
            'data_validacao': time.strftime('%Y-%m-%d %H:%M:%S'),
            'extensao_valida': False,
            'sem_senha': False,
            'tamanho_arquivo_ok': False,
            'tamanho_pagina_ok': False,
            'assinado': False,
            'numero_assinaturas': 0,
            'autenticidade_ok': False,
            'integridade_ok': False,
            'pesquisavel': False,
            'resultado_final': 'ERRO',
            'pontuacao': 0,
            'titular_certificado': '',
            'emissor_certificado': '',
            'validade_certificado': '',
            'mensagem_erro': '',
        }

        page_html = ''
        page_text = ''
        try:
            page_html = driver.page_source or ''
            page_text = (driver.find_element(By.TAG_NAME, 'body').text or '')
        except Exception:
            pass

        # Salva HTML de debug somente quando explicitamente habilitado
        if os.getenv('TCEES_SAVE_DEBUG_HTML') == '1':
            try:
                with open('tcees_debug.html', 'w', encoding='utf-8') as debug_file:
                    debug_file.write(page_html)
            except Exception as debug_error:
                print(f'   [⚠️] Não foi possível salvar tcees_debug.html: {debug_error}')

        statuses = parsed_statuses
        if not statuses:
            statuses = _extract_statuses_from_driver(driver)
        if not statuses:
            statuses = _extract_statuses_from_html(page_html)

        parsed_ok = _apply_statuses_to_results(results, statuses, page_text=page_text)

        if not parsed_ok:
            # Fallback minimo para evitar "todos X" por falha tecnica de parse
            results['extensao_valida'] = os.path.splitext(pdf_path)[1].lower() == '.pdf'
            results['tamanho_arquivo_ok'] = os.path.getsize(pdf_path) > 0
            results['mensagem_erro'] = 'Não foi possível interpretar a resposta do TCEES.'
            results['resultado_final'] = 'ERRO'
            print('   [⚠️] Falha ao extrair os 8 indicadores do TCEES.')

        campos_ok = sum([
            results['extensao_valida'],
            results['sem_senha'],
            results['tamanho_arquivo_ok'],
            results['tamanho_pagina_ok'],
            results['assinado'],
            results['autenticidade_ok'],
            results['pesquisavel'],
        ])
        results['pontuacao'] = int((campos_ok / 7) * 100)

        print('\n   [📋] RESULTADOS DA VALIDAÇÃO:')
        print(f"      Arquivo: {results['nome_arquivo']}")
        print(f"      Extensão válida: {results['extensao_valida']}")
        print(f"      Sem senha: {results['sem_senha']}")
        print(f"      Tamanho arquivo OK: {results['tamanho_arquivo_ok']}")
        print(f"      Tamanho página OK: {results['tamanho_pagina_ok']}")
        print(f"      Assinado: {results['assinado']} ({results['numero_assinaturas']} assinatura(s))")
        print(f"      Autenticidade: {results['autenticidade_ok']}")
        print(f"      Integridade: {results['integridade_ok']}")
        print(f"      Pesquisável: {results['pesquisavel']}")
        print(f"      Resultado final: {results['resultado_final']}")
        print(f"      Pontuação: {results['pontuacao']}/100")

        return results

    except Exception as error:
        error_code, friendly_error = _friendly_network_error(error)
        print(f'   [❌] ERRO na validação TCEES: {error}')

        if driver:
            try:
                screenshot_path = os.path.join(os.path.dirname(pdf_path), 'tcees_error_screenshot.png')
                driver.save_screenshot(screenshot_path)
                print(f'   [📸] Screenshot salvo: {screenshot_path}')
            except Exception:
                pass

        return {
            'nome_arquivo': os.path.basename(pdf_path),
            'extensao_valida': False,
            'sem_senha': False,
            'tamanho_arquivo_ok': False,
            'tamanho_pagina_ok': False,
            'assinado': False,
            'numero_assinaturas': 0,
            'autenticidade_ok': False,
            'integridade_ok': False,
            'pesquisavel': False,
            'resultado_final': 'ERRO',
            'pontuacao': 0,
            'titular_certificado': '',
            'emissor_certificado': '',
            'validade_certificado': '',
            'erro': friendly_error or str(error),
            'erro_codigo': error_code or 'TCEES_VALIDATION_ERROR',
            'erro_tecnico': str(error),
        }

    finally:
        if driver:
            try:
                driver.quit()
                print('   [🔒] Navegador fechado\n')
            except Exception:
                pass


def validate_multiple_pdfs(pdf_paths, max_workers=3):
    """Valida multiplos PDFs em paralelo (maximo de 3)."""
    if not pdf_paths:
        return []

    pdf_paths = pdf_paths[:3]

    print(f"\n[🚀] Iniciando validação paralela de {len(pdf_paths)} documento(s)...")
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=min(max_workers, len(pdf_paths))) as executor:
        try:
            results = list(executor.map(validate_pdf_with_tcees, pdf_paths))
        except Exception as error:
            print(f'[❌] Erro na validação paralela: {error}')
            results = []
            for pdf_path in pdf_paths:
                results.append({
                    'nome_arquivo': os.path.basename(pdf_path),
                    'resultado_final': 'ERRO',
                    'erro': str(error),
                })

    total_time = time.time() - start_time
    print(
        f"\n[✅] Validação paralela concluída em {total_time:.1f}s "
        f"(média: {total_time / len(pdf_paths):.1f}s por documento)"
    )

    return results


def test_tcees_validation():
    """Funcao de teste local."""
    print('[🧪] TESTE DE VALIDAÇÃO TCEES')
    print('=' * 60)

    test_dir = os.path.dirname(os.path.abspath(__file__))
    uploads_dir = os.path.join(test_dir, 'uploads')

    if os.path.exists(uploads_dir):
        pdfs = [filename for filename in os.listdir(uploads_dir) if filename.lower().endswith('.pdf')]
        if pdfs:
            test_file = os.path.join(uploads_dir, pdfs[0])
            print(f'[📄] Testando com: {pdfs[0]}\n')
            start = time.time()
            result = validate_pdf_with_tcees(test_file)
            elapsed = time.time() - start
            print(f"\n[✅] Teste concluído em {elapsed:.1f}s")
            return result

    print('[❌] Nenhum arquivo PDF encontrado para teste')
    return None


if __name__ == '__main__':
    test_tcees_validation()
