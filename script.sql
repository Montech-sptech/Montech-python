DROP DATABASE montech;

CREATE DATABASE IF NOT EXISTS montech;

USE montech;

CREATE TABLE empresa (
  idEmpresa INT NOT NULL AUTO_INCREMENT,
  razaoSocial VARCHAR(200) NOT NULL,
  cnpj CHAR(14) NOT NULL,
  email VARCHAR(200),
  cep CHAR(8),
  numero VARCHAR(10),
  statusAtividade TINYINT NOT NULL DEFAULT 1,
  PRIMARY KEY (idEmpresa),
  UNIQUE KEY uq_empresa_cnpj (cnpj)
);

CREATE TABLE aeroporto (
  idAeroporto INT NOT NULL AUTO_INCREMENT,
  nome VARCHAR(100) NOT NULL,
  codigoAeroporto VARCHAR(10) NOT NULL,
  statusAtividade TINYINT NOT NULL DEFAULT 1,
  urlJira VARCHAR(255) NOT NULL,
  emailJira VARCHAR(255) NOT NULL,
  tokenJira VARCHAR(255) NOT NULL,
  fkEmpresa INT NOT NULL,
  PRIMARY KEY (idAeroporto),
  UNIQUE KEY uq_aeroporto_empresa_codigo (fkEmpresa, codigoAeroporto),
  CONSTRAINT fk_aeroporto_empresa FOREIGN KEY (fkEmpresa)
    REFERENCES empresa (idEmpresa)
);

CREATE TABLE cargo (
  idCargo INT NOT NULL AUTO_INCREMENT,
  nome VARCHAR(100) NOT NULL,
  descricao VARCHAR(250),
  statusAtividade TINYINT NOT NULL DEFAULT 1,
  empresa_idEmpresa INT NOT NULL,
  PRIMARY KEY (idCargo),
  UNIQUE KEY uq_cargo_nome (nome),
  CONSTRAINT fk_cargo_empresa FOREIGN KEY (empresa_idEmpresa)
    REFERENCES empresa (idEmpresa)
);

CREATE TABLE permissao (
  idPermissao INT NOT NULL AUTO_INCREMENT,
  nome VARCHAR(100) NOT NULL,
  descricao VARCHAR(250),
  PRIMARY KEY (idPermissao),
  UNIQUE KEY uq_permissao_nome (nome)
);

CREATE TABLE componente (
  idComponente INT NOT NULL AUTO_INCREMENT,
  nomeComponente VARCHAR(100) NOT NULL,
  unidadeMedida VARCHAR(30) NOT NULL,
  codigo VARCHAR(255) NOT NULL,
  nomeCodigo VARCHAR(50) NOT NULL,
  PRIMARY KEY (idComponente),
  UNIQUE KEY uq_componente_nome (nomeComponente)
);

CREATE TABLE servidor (
  idServidor INT NOT NULL AUTO_INCREMENT,
  token VARCHAR(32) UNIQUE NOT NULL,
  nomeServidor VARCHAR(100) NOT NULL,
  sistemaOperacional VARCHAR(100) NOT NULL,
  intervaloColeta INT DEFAULT NULL,
  statusAtividade TINYINT NOT NULL DEFAULT 0,
  fkAeroporto INT NOT NULL,
  PRIMARY KEY (idServidor),
  CONSTRAINT fk_servidor_aeroporto FOREIGN KEY (fkAeroporto)
    REFERENCES aeroporto (idAeroporto),
  CONSTRAINT ck_servidor_intervalo
    CHECK (intervaloColeta IS NULL OR intervaloColeta > 0)
);


CREATE TABLE usuario (
  idUsuario INT NOT NULL AUTO_INCREMENT,
  nome VARCHAR(100) NOT NULL,
  email VARCHAR(200) NOT NULL,
  senha VARCHAR(50) NOT NULL,
  cpf CHAR(11) NOT NULL,
  telefone VARCHAR(20),
  statusAtividade TINYINT NOT NULL DEFAULT 1,
  fkCargo INT NOT NULL,
  fkAeroporto INT NOT NULL,
  PRIMARY KEY (idUsuario),
  UNIQUE KEY uq_usuario_email (email),
  UNIQUE KEY uq_usuario_cpf (cpf),
  CONSTRAINT fk_usuario_cargo FOREIGN KEY (fkCargo)
    REFERENCES cargo (idCargo),
  CONSTRAINT fk_usuario_aeroporto FOREIGN KEY (fkAeroporto)
    REFERENCES aeroporto (idAeroporto)
);

CREATE TABLE servidorcomponente (
  idServidorComponente INT NOT NULL AUTO_INCREMENT,
  fkServidor INT NOT NULL,
  fkComponente INT NOT NULL,
  limiteAtencao DECIMAL(10,2),
  limiteCritico DECIMAL(10,2),
  PRIMARY KEY (idServidorComponente),
  UNIQUE KEY uq_servidor_componente_instancia
    (fkServidor, fkComponente),
  CONSTRAINT fk_servidorcomponente_servidor FOREIGN KEY (fkServidor)
    REFERENCES servidor (idServidor),
  CONSTRAINT fk_servidorcomponente_componente FOREIGN KEY (fkComponente)
    REFERENCES componente (idComponente),
  CONSTRAINT ck_servidorcomponente_limites CHECK (
    limiteAtencao IS NOT NULL OR limiteCritico IS NOT NULL
    OR limiteAtencao <= limiteCritico
  )
);

CREATE TABLE chamado (
  idChamado INT NOT NULL AUTO_INCREMENT,
  titulo VARCHAR(200) NOT NULL,
  descricao VARCHAR(255) NOT NULL,
  statusChamado ENUM('ABERTO', 'FECHADO') NOT NULL DEFAULT 'ABERTO',
  tipoChamado ENUM('ATENCAO', 'CRITICO') NOT NULL DEFAULT 'ATENCAO',
  linkJira VARCHAR(500) NOT NULL,
  dt_inicio DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  dt_fim DATETIME,
  fkServidorComponente INT NOT NULL,
  PRIMARY KEY (idChamado, fkServidorComponente),
  CONSTRAINT fk_chamado_servidorcomponente FOREIGN KEY (fkServidorComponente)
    REFERENCES servidorcomponente (idServidorComponente),
  CONSTRAINT ck_chamado_datas CHECK (
    dt_fim IS NULL OR dt_fim >= dt_inicio
  )
);


CREATE TABLE cargopermissao (
  idCargoPermissao INT NOT NULL AUTO_INCREMENT,
  fkCargo INT NOT NULL,
  fkPermissao INT NOT NULL,
  dataConcessao DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (idCargoPermissao),
  UNIQUE KEY uq_cargopermissao (fkCargo, fkPermissao),
  CONSTRAINT fk_cargopermissao_cargo FOREIGN KEY (fkCargo)
    REFERENCES cargo (idCargo),
  CONSTRAINT fk_cargopermissao_permissao FOREIGN KEY (fkPermissao)
    REFERENCES permissao (idPermissao)
);

CREATE TABLE visualizacao (
  idVisualizacao INT NOT NULL AUTO_INCREMENT,
  fkUsuario INT NOT NULL,
  fkServidor INT NOT NULL,
  dataInicioAcesso DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  dataFimAcesso DATETIME,
  PRIMARY KEY (idVisualizacao),
  UNIQUE KEY uq_visualizacao_usuario_servidor (fkUsuario, fkServidor),
  CONSTRAINT fk_visualizacao_usuario FOREIGN KEY (fkUsuario)
    REFERENCES usuario (idUsuario),
  CONSTRAINT fk_visualizacao_servidor FOREIGN KEY (fkServidor)
    REFERENCES servidor (idServidor),
  CONSTRAINT ck_visualizacao_periodo CHECK (
    dataFimAcesso IS NULL OR dataFimAcesso >= dataInicioAcesso
  )
);

-- ------------------------------------------------------------
-- EMPRESA (ids 1 e 2)
-- ------------------------------------------------------------
INSERT INTO empresa (razaoSocial, cnpj, email, cep, numero, statusAtividade) VALUES
('Aeroportos Paulistas S.A.',    '11222333000181', 'contato@aeroportospaulistas.com.br', '01310100', '1578', 1),  -- 1
('Campinas Aero Operações LTDA', '45723174000110', 'ti@campinasaero.com.br',             '13054750', '300',  1);  -- 2
 
-- ------------------------------------------------------------
-- AEROPORTO (ids 1 a 3) -> fkEmpresa
-- ------------------------------------------------------------
INSERT INTO aeroporto (nome, codigoAeroporto, statusAtividade, urlJira, emailJira, tokenJira, fkEmpresa) VALUES
('Aeroporto Internacional de Guarulhos', 'GRU', 1, "https://montechsd.atlassian.net", "botmontech@protonmail.com", "ATATT3xFfGF0MWo_zxNbNrvWLE8wpvRB55h4rCyyNi6sECykHLR_yjItk9siiVcZ8iKrBWsWx21-UlRgeKEDexxvERCGRC7nKLfky_i-rvl9kL0DBDS1EIQ5bBoVTHMvksNY_uoZ9_YRkBBe_y_cyPe_HRPlsGTpK8VmSBM49qJCK3zbi3zwxSY=0F6A4BB3" ,1),  -- 1 (Aeroportos Paulistas)
('Aeroporto de Congonhas',               'CGH', 1, "https://montechsd.atlassian.net", "botmontech@protonmail.com", "ATATT3xFfGF0MWo_zxNbNrvWLE8wpvRB55h4rCyyNi6sECykHLR_yjItk9siiVcZ8iKrBWsWx21-UlRgeKEDexxvERCGRC7nKLfky_i-rvl9kL0DBDS1EIQ5bBoVTHMvksNY_uoZ9_YRkBBe_y_cyPe_HRPlsGTpK8VmSBM49qJCK3zbi3zwxSY=0F6A4BB3" ,1),  -- 2 (Aeroportos Paulistas)
('Aeroporto Internacional de Viracopos', 'VCP', 1, "https://montechsd.atlassian.net", "botmontech@protonmail.com", "ATATT3xFfGF0MWo_zxNbNrvWLE8wpvRB55h4rCyyNi6sECykHLR_yjItk9siiVcZ8iKrBWsWx21-UlRgeKEDexxvERCGRC7nKLfky_i-rvl9kL0DBDS1EIQ5bBoVTHMvksNY_uoZ9_YRkBBe_y_cyPe_HRPlsGTpK8VmSBM49qJCK3zbi3zwxSY=0F6A4BB3" ,2);  -- 3 (Campinas Aero)

-- ------------------------------------------------------------
-- CARGO (ids 1 a 5) -> empresa_idEmpresa
-- ------------------------------------------------------------
INSERT INTO cargo (nome, descricao, statusAtividade, empresa_idEmpresa) VALUES
('Administrador',              'Acesso total: gerencia usuários, servidores, limites e relatórios',       1, 1),  -- 1
('Analista de Infraestrutura', 'Configura servidores e limites e acompanha a saúde da infraestrutura',    1, 1),  -- 2
('Operador de Monitoramento',  'Acompanha os painéis em tempo real e sinaliza incidentes',                1, 1),  -- 3
('Gerente de TI',              'Responsável pela área de TI do aeroporto, gestão de equipe e relatórios', 1, 2),  -- 4
('Técnico de Campo',           'Realiza manutenção e cadastro físico dos servidores',                     1, 2);  -- 5
 
-- ------------------------------------------------------------
-- PERMISSAO (ids 1 a 5)
-- ------------------------------------------------------------
INSERT INTO permissao (nome, descricao) VALUES
('VISUALIZAR_DASHBOARD', 'Permite visualizar os painéis de monitoramento'),             -- 1
('GERENCIAR_SERVIDORES', 'Permite cadastrar, editar e desativar servidores'),           -- 2
('GERENCIAR_USUARIOS',   'Permite cadastrar, editar e desativar usuários'),             -- 3
('CONFIGURAR_LIMITES',   'Permite definir os limites de atenção e crítico dos componentes'),  -- 4
('EXPORTAR_RELATORIOS',  'Permite gerar e exportar relatórios de desempenho'),          -- 5
('VISUALIZAR_DASHBOARD_GESTOR', 'Permite visualizar o painel gerencial');               -- 6 
-- ------------------------------------------------------------
-- CARGOPERMISSAO -> (fkCargo, fkPermissao)
-- ------------------------------------------------------------
INSERT INTO cargopermissao (fkCargo, fkPermissao) VALUES
-- Administrador: tudo
(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6),
-- Analista de Infraestrutura: dashboard, servidores, limites, relatórios
(2, 1), (2, 2), (2, 4), (2, 5),
-- Operador de Monitoramento: só dashboard
(3, 1),
-- Gerente de TI: dashboard, usuários, limites, relatórios
(4, 1), (4, 3), (4, 4), (4, 5), (4, 6),
-- Técnico de Campo: dashboard e servidores
(5, 1), (5, 2);
 
-- ------------------------------------------------------------
-- USUARIO (ids 1 a 5) -> fkCargo, fkAeroporto
-- Senha em texto puro porque o coletor compara senha = %s direto.
-- Cargo e aeroporto sempre da mesma empresa.
-- ------------------------------------------------------------
INSERT INTO usuario (nome, email, senha, cpf, telefone, statusAtividade, fkCargo, fkAeroporto) VALUES
('Carlos Silva',      'carlos.silva@aeroportospaulistas.com.br',  'Admin@123',    '11144477735', '11987654321', 1, 1, 1),
('Mariana Souza',     'mariana.souza@aeroportospaulistas.com.br', 'Analista@123', '52998224725', '11976543210', 1, 2, 1),
('Rafael Lima',       'rafael.lima@aeroportospaulistas.com.br',   'Operador@123', '39053344705', '11965432109', 1, 3, 2),
('Fernanda Oliveira', 'fernanda.oliveira@campinasaero.com.br',    'Gerente@123',  '16899535009', '19987654321', 1, 4, 3),
('João Pereira',      'joao.pereira@campinasaero.com.br',         'Tecnico@123',  '71428793860', '19976543210', 1, 5, 3);
 
-- ------------------------------------------------------------
-- COMPONENTE (ids 1 a 11)
-- "codigo" = expressão Python executada pelo eval() do coletor
-- "nomeCodigo" = nome da coluna no CSV
-- ------------------------------------------------------------
INSERT INTO componente (nomeComponente, unidadeMedida, codigo, nomeCodigo) VALUES
('Uso de CPU (Geral)', 'porcentagem', 'psutil.cpu_percent(interval=1)', 'UsoCPU_Geral'),
('Uso de RAM', 'porcentagem', 'psutil.virtual_memory().percent', 'UsoRAM'),
('Swap In', 'bytes', 'psutil.swap_memory().sin', 'Swap_In'),
('Swap Out', 'bytes', 'psutil.swap_memory().sout', 'Swap_Out'),
('Uso de Disco', 'porcentagem', 'psutil.disk_usage("C:\\\\" if os.name == "nt" else "/").percent', 'UsoDisco'),
('Leitura de Disco', 'bytes', 'psutil.disk_io_counters().read_bytes', 'Disco_Read_Bytes'),
('Escrita de Disco', 'bytes', 'psutil.disk_io_counters().write_bytes', 'Disco_Write_Bytes'),
('Rede - Bytes Enviados', 'bytes', 'psutil.net_io_counters().bytes_sent', 'Rede_Bytes_Enviados'),
('Rede - Bytes Recebidos', 'bytes', 'psutil.net_io_counters().bytes_recv', 'Rede_Bytes_Recebidos'),
('Rede - Pacotes Enviados', 'pacotes', 'psutil.net_io_counters().packets_sent', 'Rede_Pacotes_Enviados'),
('Rede - Pacotes Recebidos', 'pacotes', 'psutil.net_io_counters().packets_recv', 'Rede_Pacotes_Recebidos'),
('Temperatura da CPU', 'celsius', 'psutil.sensors_temperatures()', 'Temperatura_CPU'),
('Processos Ativos', 'processos', 'len(psutil.pids())', 'Qtd_Processos'),


-- Adicionais para predição (apenas os usados no script R)
('RAM - Livre', 'bytes', 'psutil.virtual_memory().free', 'RAM_Livre'),
('Troca de Contexto da CPU', 'contagem', 'psutil.cpu_stats().ctx_switches', 'CPU_Ctx_Switches'),
('Interrupções da CPU', 'contagem', 'psutil.cpu_stats().interrupts', 'CPU_Interrupts'),
('Chamadas de Sistema', 'contagem', 'psutil.cpu_stats().syscalls', 'CPU_Syscalls'),
('Disco - Operações de Escrita', 'contagem', 'psutil.disk_io_counters().write_count', 'Disco_Write_Count');
-- ------------------------------------------------------------
-- SERVIDOR (ids 1 a 4) -> fkAeroporto
-- token com exatamente 32 caracteres; intervaloColeta em segundos (NULL = padrão)
-- ------------------------------------------------------------
INSERT INTO servidor (token, nomeServidor, sistemaOperacional, intervaloColeta, statusAtividade, fkAeroporto) VALUES
('3b12f1df-5232-4df4-8d48-64a938c2', 'SDV-GRU-01', 'linux', 3, 1, 1),  -- 1 GRU (servidor de teste)
('5d2e8b7a1c4f4963b0a7e21f9c6d3a85', 'SPA-GRU-01', 'windows', 3, 1, 1),  -- 2 GRU
('9b7c3e5f2a1d48c6847f0e3b6a2d1c90', 'AIS-CGH-01', 'windows', 3, 1, 2),  -- 3 CGH
('c4e1a7b92d3f45068e9a1b7c5d3f2e64', 'SPA-VCP-01', 'linux', 3, 1, 3),  -- 4 VCP
('a7f3c9e21b5d4680a2c7f1e9b6d3a485', 'SDV-GRU-02', 'linux', 3, 1, 1); -- 5 TUDO
 
-- ------------------------------------------------------------
-- SERVIDORCOMPONENTE -> (fkServidor, fkComponente, limiteAtencao, limiteCritico)
-- Limite NULL = métrica sem limite (lista por core, contadores acumulados...)
-- ------------------------------------------------------------
INSERT INTO servidorcomponente (fkServidor, fkComponente, limiteAtencao, limiteCritico) VALUES
(1, 1,  70.00,  90.00),   -- Uso de CPU (Geral)
(1, 2,  75.00,  90.00),   -- Uso de RAM
(1, 3,  80.00,  90.00),   -- Swap In
(1, 4,  80.00,  90.00),   -- Swap Out
(1, 5,  80.00,  95.00),   -- Uso de Disco
(1, 6,  80.00,  90.00),   -- Leitura de Disco
(1, 7,  80.00,  90.00),   -- Escrita de Disco
(1, 8,  80.00,  90.00),   -- Rede - Bytes Enviados
(1, 9,  80.00,  90.00),   -- Rede - Bytes Recebidos
(1, 10, 80.00,  90.00),   -- Rede - Pacotes Enviados
(1, 11, 80.00,  90.00),   -- Rede - Pacotes Recebidos
(1, 12, 75.00,  90.00),   -- Temperatura da CPU
(1, 13, 300.00, 500.00),  -- Processos Ativos
(1, 14, 80.00,  90.00),   -- RAM - Livre
(1, 15, NULL,   NULL),    -- Troca de Contexto da CPU
(1, 16, NULL,   NULL),    -- Interrupções da CPU
(1, 17, NULL,   NULL),    -- Chamadas de Sistema
(1, 18, NULL,   NULL),    -- Disco - Operações de Escrita
 
-- Servidor 2 (SPA-GRU-01): RAM, disco e rede recebida
(2, 2,  80.00,  90.00),   -- Uso de RAM
(2, 5,  80.00,  95.00),   -- Uso de Disco
(2, 6,  80.00,   90.00),    -- Leitura de Disco
(2, 7,  80.00,   90.00),    -- Escrita de Disco
(2, 9, 80.00,   90.00),    -- Rede - Bytes Recebidos
 
-- Servidor 3 (AIS-CGH-01): RAM, disco e rede
(3, 2,  80.00,  92.00),   -- Uso de RAM
(3, 5,  80.00,  95.00),   -- Uso de Disco
(3, 6,  80.00,   90.00),    -- Leitura de Disco
(3, 7,  80.00,   90.00),    -- Escrita de Disco
(3, 8,  80.00,   90.00),    -- Rede - Bytes Enviados
(3, 9, 80.00,   90.00),    -- Rede - Bytes Recebidos
 
-- Servidor 4 (SPA-VCP-01): RAM e disco
(4, 2,  80.00,  92.00),   -- Uso de RAM
(4, 5,  85.00,  95.00),   -- Uso de Disco
(4, 6,  80.00,   90.00),    -- Leitura de Disco
(4, 7,  80.00,   90.00),    -- Escrita de Disco

-- Servidor 5 (SDV-GRU-02): todos os 11 componentes
(5, 1,  70.00,  90.00),   -- Uso de CPU (Geral)
(5, 2,  75.00,  90.00),   -- Uso de RAM
(5, 3,  0.00,   0.00),    -- Swap In
(5, 4,  0.00,   0.00),    -- Swap Out
(5, 5,  80.00,  95.00),   -- Uso de Disco
(5, 6,  0.00,   0.00),    -- Leitura de Disco
(5, 7,  0.00,   0.00),    -- Escrita de Disco
(5, 8,  0.00,   0.00),    -- Rede - Bytes Enviados
(5, 9,  0.00,   0.00),    -- Rede - Bytes Recebidos
(5, 10, 0.00,   0.00),    -- Rede - Pacotes Enviados
(5, 11, 0.00,   0.00),    -- Rede - Pacotes Recebidos
(5, 12, 70.00,  85.00),   -- Temperatura da CPU
(5, 13, 300.00, 500.00),  -- Processos Ativos

-- Adicionais para predição (apenas os usados no script R)
(5, 14, NULL,   NULL),    -- RAM - Livre
(5, 15, NULL,   NULL),    -- Troca de Contexto da CPU
(5, 16, NULL,   NULL),    -- Interrupções da CPU
(5, 17, NULL,   NULL),    -- Chamadas de Sistema
(5, 18, NULL,   NULL);    -- Disco - Operações de Escrita
 
-- ------------------------------------------------------------
-- VISUALIZACAO -> (fkUsuario, fkServidor, dataInicioAcesso, dataFimAcesso)
-- dataFimAcesso NULL = acesso ainda ativo
-- Cada usuário só visualiza servidores da própria empresa.
-- ------------------------------------------------------------

/* =====================================================================
   MonTech - Chamados de exemplo (Jira simulado)
   Cobre 12/07/2026 a 19/11/2026 para testar a dashboard "Resposta aos incidentes".
   Periodo de teste: 04/10 a 31/10/2026 (anterior: 06/09 a 03/10) no aeroporto 1 (GRU),
   que tem 3 servidores (1, 2 e 5). Os servidores 3 (CGH) e 4 (VCP) so tem alguns
   chamados de "ruido" para provar que o filtro por aeroporto funciona.
   O id do servidorcomponente e buscado por (fkServidor, fkComponente), entao nao
   depende da ordem dos seus INSERTs. Os links do Jira sao ficticios.
   Rode DEPOIS dos INSERTs de servidor e servidorcomponente.
   ===================================================================== */

INSERT INTO chamado
  (titulo, descricao, statusChamado, tipoChamado, linkJira, dt_inicio, dt_fim, fkServidorComponente)
VALUES
('Uso de RAM acima do limite crítico - SPA-GRU-01', 'Uso de RAM em 92,5% (limite crítico: 90%) no servidor SPA-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-101', '2026-07-13 12:00:00', '2026-07-13 14:28:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 2 AND fkComponente = 2)),
('Uso de RAM acima do limite de atenção - SDV-GRU-01', 'Uso de RAM em 82,5% (limite de atenção: 75%) no servidor SDV-GRU-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-102', '2026-07-18 17:00:00', '2026-07-18 20:20:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 2)),
('Uso de RAM acima do limite de atenção - SDV-GRU-02', 'Uso de RAM em 81,1% (limite de atenção: 75%) no servidor SDV-GRU-02.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-103', '2026-07-19 16:00:00', '2026-07-19 19:18:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 2)),
('Uso de RAM acima do limite de atenção - SDV-GRU-01', 'Uso de RAM em 82,0% (limite de atenção: 75%) no servidor SDV-GRU-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-104', '2026-07-29 13:00:00', '2026-07-29 16:14:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 2)),
('Uso de RAM acima do limite crítico - AIS-CGH-01', 'Uso de RAM em 96,3% (limite crítico: 92%) no servidor AIS-CGH-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-105', '2026-07-30 10:00:00', '2026-07-30 14:20:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 3 AND fkComponente = 2)),
('Temperatura da CPU acima do limite crítico - SDV-GRU-01', 'Temperatura da CPU em 91,3°C (limite crítico: 90°C) no servidor SDV-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-106', '2026-07-31 20:00:00', '2026-08-01 00:49:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 12)),
('Uso de CPU acima do limite de atenção - SDV-GRU-01', 'Uso de CPU em 80,1% (limite de atenção: 70%) no servidor SDV-GRU-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-107', '2026-08-01 18:00:00', '2026-08-01 21:54:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 1)),
('Uso de Disco acima do limite de atenção - SDV-GRU-01', 'Uso de Disco em 84,5% (limite de atenção: 80%) no servidor SDV-GRU-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-108', '2026-08-04 13:00:00', '2026-08-04 17:38:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 5)),
('Uso de CPU acima do limite crítico - SDV-GRU-01', 'Uso de CPU em 92,7% (limite crítico: 90%) no servidor SDV-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-109', '2026-08-06 18:00:00', '2026-08-06 21:46:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 1)),
('Uso de Disco acima do limite crítico - SDV-GRU-02', 'Uso de Disco em 101,3% (limite crítico: 95%) no servidor SDV-GRU-02.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-110', '2026-08-10 03:00:00', '2026-08-10 07:59:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 5)),
('Uso de RAM acima do limite crítico - SDV-GRU-01', 'Uso de RAM em 93,2% (limite crítico: 90%) no servidor SDV-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-111', '2026-08-13 09:00:00', '2026-08-13 13:07:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 2)),
('Uso de Disco acima do limite de atenção - SDV-GRU-02', 'Uso de Disco em 91,7% (limite de atenção: 80%) no servidor SDV-GRU-02.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-112', '2026-08-17 11:00:00', '2026-08-17 15:03:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 5)),
('Uso de Disco acima do limite de atenção - AIS-CGH-01', 'Uso de Disco em 83,7% (limite de atenção: 80%) no servidor AIS-CGH-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-113', '2026-08-20 03:00:00', '2026-08-20 06:10:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 3 AND fkComponente = 5)),
('Temperatura da CPU acima do limite de atenção - SDV-GRU-02', 'Temperatura da CPU em 78,0°C (limite de atenção: 70°C) no servidor SDV-GRU-02.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-114', '2026-08-23 18:00:00', '2026-08-23 21:19:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 12)),
('Temperatura da CPU acima do limite crítico - SDV-GRU-01', 'Temperatura da CPU em 95,2°C (limite crítico: 90°C) no servidor SDV-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-115', '2026-08-26 21:00:00', '2026-08-27 01:11:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 12)),
('Uso de RAM acima do limite crítico - SPA-GRU-01', 'Uso de RAM em 95,3% (limite crítico: 90%) no servidor SPA-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-116', '2026-09-02 18:00:00', '2026-09-02 20:25:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 2 AND fkComponente = 2)),
('Uso de Disco acima do limite de atenção - SDV-GRU-01', 'Uso de Disco em 86,1% (limite de atenção: 80%) no servidor SDV-GRU-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-117', '2026-09-04 07:00:00', '2026-09-04 10:17:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 5)),
('Uso de RAM acima do limite crítico - SDV-GRU-01', 'Uso de RAM em 92,8% (limite crítico: 90%) no servidor SDV-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-118', '2026-09-08 21:00:00', '2026-09-09 00:20:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 2)),
('Uso de RAM acima do limite crítico - SPA-VCP-01', 'Uso de RAM em 95,7% (limite crítico: 92%) no servidor SPA-VCP-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-119', '2026-09-12 15:00:00', '2026-09-12 20:10:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 4 AND fkComponente = 2)),
('Processos Ativos acima do limite crítico - SDV-GRU-02', 'Processos Ativos em 529 processos (limite crítico: 500 processos) no servidor SDV-GRU-02.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-120', '2026-09-15 09:00:00', '2026-09-15 11:30:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 13)),
('Uso de RAM acima do limite crítico - SPA-GRU-01', 'Uso de RAM em 91,3% (limite crítico: 90%) no servidor SPA-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-121', '2026-09-19 09:00:00', '2026-09-19 12:48:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 2 AND fkComponente = 2)),
('Uso de RAM acima do limite de atenção - SPA-GRU-01', 'Uso de RAM em 82,6% (limite de atenção: 80%) no servidor SPA-GRU-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-122', '2026-09-20 03:00:00', '2026-09-20 05:58:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 2 AND fkComponente = 2)),
('Uso de Disco acima do limite crítico - SDV-GRU-01', 'Uso de Disco em 97,5% (limite crítico: 95%) no servidor SDV-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-123', '2026-09-29 20:00:00', '2026-09-29 23:12:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 5)),
('Uso de Disco acima do limite crítico - SDV-GRU-02', 'Uso de Disco em 99,9% (limite crítico: 95%) no servidor SDV-GRU-02.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-124', '2026-10-02 03:00:00', '2026-10-02 08:12:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 5)),
('Rede - Bytes Recebidos acima do limite crítico - AIS-CGH-01', 'Rede - Bytes Recebidos em 91,3% (limite crítico: 90%) no servidor AIS-CGH-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-125', '2026-10-02 21:30:00', '2026-10-03 02:10:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 3 AND fkComponente = 9)),
('Uso de CPU acima do limite crítico - SDV-GRU-01', 'Uso de CPU em 94,8% (limite crítico: 90%) no servidor SDV-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-126', '2026-10-06 09:00:00', '2026-10-06 12:10:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 1)),
('Uso de CPU acima do limite crítico - SDV-GRU-01', 'Uso de CPU em 92,6% (limite crítico: 90%) no servidor SDV-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-127', '2026-10-06 20:00:00', '2026-10-07 02:50:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 1)),
('Uso de RAM acima do limite de atenção - AIS-CGH-01', 'Uso de RAM em 86,6% (limite de atenção: 80%) no servidor AIS-CGH-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-128', '2026-10-09 07:00:00', '2026-10-09 09:30:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 3 AND fkComponente = 2)),
('Uso de RAM acima do limite de atenção - SDV-GRU-02', 'Uso de RAM em 84,1% (limite de atenção: 75%) no servidor SDV-GRU-02.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-129', '2026-10-13 20:00:00', '2026-10-13 23:00:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 2)),
('Uso de RAM acima do limite crítico - SDV-GRU-02', 'Uso de RAM em 93,3% (limite crítico: 90%) no servidor SDV-GRU-02.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-130', '2026-10-14 02:00:00', '2026-10-14 06:30:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 2)),
('Uso de RAM acima do limite crítico - SDV-GRU-02', 'Uso de RAM em 94,8% (limite crítico: 90%) no servidor SDV-GRU-02.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-131', '2026-10-14 08:00:00', '2026-10-14 10:00:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 2)),
('Uso de Disco acima do limite crítico - SPA-VCP-01', 'Uso de Disco em 101,0% (limite crítico: 95%) no servidor SPA-VCP-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-132', '2026-10-15 01:20:00', '2026-10-15 08:00:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 4 AND fkComponente = 5)),
('Uso de Disco acima do limite de atenção - SPA-GRU-01', 'Uso de Disco em 86,1% (limite de atenção: 80%) no servidor SPA-GRU-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-133', '2026-10-20 09:00:00', '2026-10-20 11:30:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 2 AND fkComponente = 5)),
('Uso de Disco acima do limite crítico - SPA-GRU-01', 'Uso de Disco em 101,3% (limite crítico: 95%) no servidor SPA-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-134', '2026-10-20 15:00:00', '2026-10-20 19:50:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 2 AND fkComponente = 5)),
('Uso de Disco acima do limite crítico - SPA-GRU-01', 'Uso de Disco em 98,0% (limite crítico: 95%) no servidor SPA-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-135', '2026-10-21 03:00:00', '2026-10-21 06:20:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 2 AND fkComponente = 5)),
('Leitura de Disco acima do limite de atenção - SPA-VCP-01', 'Leitura de Disco em 85,7% (limite de atenção: 80%) no servidor SPA-VCP-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-136', '2026-10-22 16:40:00', '2026-10-22 19:30:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 4 AND fkComponente = 6)),
('Temperatura da CPU acima do limite crítico - SDV-GRU-01', 'Temperatura da CPU em 93,6°C (limite crítico: 90°C) no servidor SDV-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-137', '2026-10-27 14:00:00', '2026-10-27 20:00:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 12)),
('Temperatura da CPU acima do limite crítico - SDV-GRU-01', 'Temperatura da CPU em 92,1°C (limite crítico: 90°C) no servidor SDV-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-138', '2026-10-28 02:00:00', '2026-10-28 06:20:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 12)),
('Temperatura da CPU acima do limite de atenção - SDV-GRU-01', 'Temperatura da CPU em 80,6°C (limite de atenção: 75°C) no servidor SDV-GRU-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-139', '2026-10-28 19:00:00', '2026-10-28 21:50:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 12)),
('Uso de Disco acima do limite crítico - AIS-CGH-01', 'Uso de Disco em 100,2% (limite crítico: 95%) no servidor AIS-CGH-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-140', '2026-10-29 11:00:00', '2026-10-29 14:50:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 3 AND fkComponente = 5)),
('Uso de CPU acima do limite crítico - SDV-GRU-02', 'Uso de CPU em 93,0% (limite crítico: 90%) no servidor SDV-GRU-02.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-141', '2026-11-03 04:30:00', '2026-11-03 08:05:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 1)),
('Uso de RAM acima do limite de atenção - SPA-VCP-01', 'Uso de RAM em 89,0% (limite de atenção: 80%) no servidor SPA-VCP-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-142', '2026-11-05 19:00:00', '2026-11-05 21:40:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 4 AND fkComponente = 2)),
('Uso de RAM acima do limite de atenção - SDV-GRU-01', 'Uso de RAM em 82,5% (limite de atenção: 75%) no servidor SDV-GRU-01.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-143', '2026-11-06 13:10:00', '2026-11-06 15:30:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 2)),
('Uso de Disco acima do limite crítico - SPA-GRU-01', 'Uso de Disco em 96,9% (limite crítico: 95%) no servidor SPA-GRU-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-144', '2026-11-10 22:00:00', '2026-11-11 03:30:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 2 AND fkComponente = 5)),
('Uso de RAM acima do limite crítico - AIS-CGH-01', 'Uso de RAM em 95,1% (limite crítico: 92%) no servidor AIS-CGH-01.', 'FECHADO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-145', '2026-11-12 05:10:00', '2026-11-12 09:10:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 3 AND fkComponente = 2)),
('Temperatura da CPU acima do limite de atenção - SDV-GRU-02', 'Temperatura da CPU em 75,5°C (limite de atenção: 70°C) no servidor SDV-GRU-02.', 'FECHADO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-146', '2026-11-13 08:45:00', '2026-11-13 10:50:00', (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 12)),
('Processos Ativos acima do limite de atenção - SDV-GRU-01', 'Processos Ativos em 356 processos (limite de atenção: 300 processos) no servidor SDV-GRU-01.', 'ABERTO', 'ATENCAO', 'https://montech.atlassian.net/browse/MON-147', '2026-11-18 20:00:00', NULL, (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 1 AND fkComponente = 13)),
('Uso de RAM acima do limite crítico - SDV-GRU-02', 'Uso de RAM em 93,2% (limite crítico: 90%) no servidor SDV-GRU-02.', 'ABERTO', 'CRITICO', 'https://montech.atlassian.net/browse/MON-148', '2026-11-19 02:15:00', NULL, (SELECT idServidorComponente FROM servidorcomponente WHERE fkServidor = 5 AND fkComponente = 2));

INSERT INTO visualizacao (fkUsuario, fkServidor, dataInicioAcesso, dataFimAcesso) VALUES
(1, 1, '2026-09-01 08:00:00', NULL),                    -- Carlos  -> SDV-GRU-01
(1, 2, '2026-09-01 08:00:00', NULL),                    -- Carlos  -> SPA-GRU-01
(1, 3, '2026-09-01 08:00:00', NULL),                    -- Carlos  -> AIS-CGH-01
(2, 1, '2026-09-02 09:30:00', NULL),                    -- Mariana -> SDV-GRU-01
(2, 2, '2026-09-02 09:30:00', NULL),                    -- Mariana -> SPA-GRU-01
(3, 3, '2026-09-03 07:45:00', '2026-09-25 18:00:00'),   -- Rafael  -> AIS-CGH-01 (acesso encerrado)
(4, 4, '2026-09-05 10:15:00', NULL),                    -- Fernanda -> SPA-VCP-01
(5, 4, '2026-09-08 13:00:00', '2026-09-20 17:30:00');   -- João    -> SPA-VCP-01 (acesso encerrado)
