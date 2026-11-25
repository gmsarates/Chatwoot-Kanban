# Personalizações do Chatwoot usadas com o Turvia (sem fork do Chatwoot).
#
# Aplica, a partir das variáveis de ambiente do serviço do Chatwoot:
#   - item "Kanban" na barra lateral (abre o Chatwoot-Kanban em tela cheia)
#   - título, nome da marca e logos
#   - ícone da aba (favicon), inclusive a versão de "mensagens não lidas"
#
# Rodar no console do serviço WEB do Chatwoot:
#   cd /app && bundle exec rails runner 'require "open-uri"; eval(URI.open("https://raw.githubusercontent.com/gmsarates/Chatwoot-Kanban/main/chatwoot/apply.rb").read)'
# ou, com o arquivo já no container:
#   cd /app && bundle exec rails runner /caminho/apply.rb
#
# Pode rodar quantas vezes quiser: ele substitui o que aplicou antes, sem duplicar.
# Variáveis (todas opcionais; o que não for definido não é alterado):
#   CW_KANBAN_URL          https://seu-kanban.up.railway.app/kanban
#   CW_KANBAN_LABEL        texto do item na barra lateral (padrão: Kanban)
#   CW_INSTALLATION_NAME   título da aba / nome do sistema (ex.: Turvia CRM)
#   CW_BRAND_NAME          nome da marca (ex.: Turvia)
#   CW_BRAND_URL           site da marca
#   CW_LOGO                logo para tema claro (tela de login)
#   CW_LOGO_DARK           logo para tema escuro
#   CW_LOGO_THUMBNAIL      ícone quadrado (canto do menu)
#   CW_FAVICON             ícone da aba (PNG quadrado)
#   CW_FAVICON_BADGE       ícone da aba com notificação (padrão: igual ao CW_FAVICON)
#   CW_UNINSTALL=1         remove os scripts (kanban + favicon) aplicados por este arquivo

require 'json'

module TurviaChatwootSetup
  BEGIN_MARK = '<!-- turvia-chatwoot:begin -->'.freeze
  END_MARK = '<!-- turvia-chatwoot:end -->'.freeze

  BRANDING_KEYS = {
    'CW_INSTALLATION_NAME' => 'INSTALLATION_NAME',
    'CW_BRAND_NAME' => 'BRAND_NAME',
    'CW_BRAND_URL' => 'BRAND_URL',
    'CW_LOGO' => 'LOGO',
    'CW_LOGO_DARK' => 'LOGO_DARK',
    'CW_LOGO_THUMBNAIL' => 'LOGO_THUMBNAIL'
  }.freeze

  module_function

  def env(name)
    value = ENV.fetch(name, '').strip
    value.empty? ? nil : value
  end

  def kanban_script(url, label)
    <<~HTML
      <script id="cw-kanban">
      (function () {
        var KANBAN_URL = #{url.to_json};
        var LABEL = #{label.to_json};
        var ID = 'cw-kanban-link';
        var overlay = null;
        var ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 7v7"/><path d="M12 7v4"/><path d="M16 7v9"/></svg>';
        function sidebar() { return document.querySelector('aside'); }
        function placeOverlay() {
          if (!overlay) return;
          var aside = sidebar();
          overlay.style.left = (aside && window.innerWidth >= 768 ? aside.getBoundingClientRect().right : 0) + 'px';
        }
        function setActive(on) {
          var a = document.getElementById(ID);
          if (a) a.style.background = on ? 'rgba(127,127,127,.18)' : '';
        }
        function closeOverlay() {
          if (!overlay) return;
          overlay.remove(); overlay = null; setActive(false);
        }
        function openOverlay() {
          if (overlay) { closeOverlay(); return; }
          var aside = sidebar();
          overlay = document.createElement('div');
          overlay.style.cssText = 'position:fixed;top:0;right:0;bottom:0;z-index:35;';
          overlay.style.background = aside ? getComputedStyle(aside).backgroundColor : '#fff';
          var frame = document.createElement('iframe');
          frame.src = KANBAN_URL;
          frame.style.cssText = 'border:0;width:100%;height:100%;display:block;';
          var close = document.createElement('button');
          close.type = 'button'; close.textContent = '\\u2715'; close.title = 'Fechar kanban';
          close.style.cssText = 'position:absolute;top:8px;right:12px;border:0;border-radius:6px;padding:2px 9px;cursor:pointer;background:rgba(127,127,127,.25);color:inherit;font-size:14px;';
          close.onclick = closeOverlay;
          overlay.appendChild(frame); overlay.appendChild(close);
          document.body.appendChild(overlay);
          placeOverlay(); setActive(true);
        }
        function ensureLink() {
          var ul = document.querySelector('aside nav ul');
          if (!ul) return;
          var collapsed = ul.classList.contains('items-center');
          var a = document.getElementById(ID);
          if (!a) {
            var li = document.createElement('li');
            li.className = 'grid min-w-0';
            a = document.createElement('a');
            a.id = ID; a.href = KANBAN_URL; a.title = LABEL;
            a.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:8px;cursor:pointer;min-width:0;color:inherit;text-decoration:none;';
            var span = document.createElement('span');
            span.setAttribute('data-label', '');
            span.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
            span.textContent = LABEL;
            a.innerHTML = ICON;
            a.appendChild(span);
            a.addEventListener('mouseenter', function () { if (!overlay) a.style.background = 'rgba(127,127,127,.12)'; });
            a.addEventListener('mouseleave', function () { if (!overlay) a.style.background = ''; });
            a.addEventListener('click', function (e) {
              if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;
              e.preventDefault(); openOverlay();
            });
            li.appendChild(a); ul.appendChild(li);
          }
          var label = a.querySelector('[data-label]');
          if (label) label.style.display = collapsed ? 'none' : '';
        }
        new MutationObserver(ensureLink).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
        document.addEventListener('click', function (e) {
          if (!overlay || !e.target.closest) return;
          var hit = e.target.closest('aside a, aside button');
          if (hit && hit.id !== ID) closeOverlay();
        }, true);
        window.addEventListener('popstate', closeOverlay);
        window.addEventListener('resize', placeOverlay);
        ensureLink();
      })();
      </script>
    HTML
  end

  def favicon_script(icon, badge)
    <<~HTML
      <script id="cw-branding">
      (function () {
        var ICON = #{icon.to_json};
        var ICON_BADGE = #{badge.to_json};
        function wanted(href) {
          if (!href) return null;
          if (href.indexOf('favicon-badge') !== -1) return ICON_BADGE;
          if (/\\/(favicon|apple-icon|android-icon|ms-icon)-/.test(href) || href.indexOf('/brand-assets/') !== -1) return ICON;
          return null;
        }
        function fix() {
          document.querySelectorAll('link[rel~="icon"], link[rel="apple-touch-icon"]').forEach(function (l) {
            var w = wanted(l.getAttribute('href'));
            if (w && l.getAttribute('href') !== w) l.setAttribute('href', w);
          });
        }
        new MutationObserver(fix).observe(document.head, { subtree: true, childList: true, attributes: true, attributeFilter: ['href'] });
        fix();
      })();
      </script>
    HTML
  end

  # Remove o bloco aplicado por este script e versões antigas coladas à mão
  # (item do kanban sem marcadores e o script de favicon).
  def strip_ours(value)
    value = value.to_s.dup.force_encoding(Encoding::UTF_8)
    value = value.gsub(/#{Regexp.escape(BEGIN_MARK)}.*?#{Regexp.escape(END_MARK)}\s*/m, '')
    value = value.gsub(%r{<script id="cw-branding">.*?</script>\s*}m, '')
    value = value.gsub(%r{<script[^>]*>(?:(?!</script>).)*cw-kanban-link(?:(?!</script>).)*</script>\s*}m, '')
    value.strip
  end

  def build_dashboard_scripts(current)
    rest = strip_ours(current)
    return rest if env('CW_UNINSTALL') == '1'

    parts = []
    kanban_url = env('CW_KANBAN_URL')
    parts << kanban_script(kanban_url, env('CW_KANBAN_LABEL') || 'Kanban') if kanban_url
    favicon = env('CW_FAVICON')
    parts << favicon_script(favicon, env('CW_FAVICON_BADGE') || favicon) if favicon
    return rest if parts.empty?

    block = "#{BEGIN_MARK}\n#{parts.join}#{END_MARK}\n"
    rest.empty? ? block : "#{rest}\n#{block}"
  end

  def set_config(name, value)
    config = InstallationConfig.find_or_initialize_by(name: name)
    return :unchanged if config.persisted? && config.value == value

    config.value = value
    config.save!
    :changed
  end

  def run
    puts '== Personalizações do Chatwoot (Turvia) =='

    BRANDING_KEYS.each do |env_name, config_name|
      value = env(env_name)
      next puts("  #{config_name}: (não definido, mantido)") unless value

      puts "  #{config_name}: #{set_config(config_name, value) == :changed ? 'atualizado' : 'já estava certo'}"
    end

    current = InstallationConfig.find_by(name: 'DASHBOARD_SCRIPTS')&.value
    scripts = build_dashboard_scripts(current)
    status = set_config('DASHBOARD_SCRIPTS', scripts)
    puts "  Item Kanban: #{env('CW_KANBAN_URL') && env('CW_UNINSTALL') != '1' ? env('CW_KANBAN_URL') : 'não aplicado'}"
    puts "  Favicon: #{env('CW_FAVICON') && env('CW_UNINSTALL') != '1' ? env('CW_FAVICON') : 'não aplicado'}"
    puts "  DASHBOARD_SCRIPTS: #{status == :changed ? 'atualizado' : 'já estava certo'}"

    GlobalConfig.clear_cache if defined?(GlobalConfig)
    puts 'Pronto. Recarregue o Chatwoot com Ctrl+Shift+R.'
  end
end

TurviaChatwootSetup.run unless ENV['TURVIA_SETUP_NO_RUN'] == '1'
