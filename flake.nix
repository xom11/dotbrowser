{
  description = "Manage browser settings as dotfiles (Brave, Vivaldi, Edge)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      mkDotbrowser = pkgs: pkgs.python3Packages.buildPythonApplication {
        pname = "dotbrowser";
        version = builtins.head
          (builtins.match ''.*__version__ = "([^"]+)".*''
            (builtins.readFile ./src/dotbrowser/__init__.py));
        pyproject = true;
        src = ./.;
        build-system = [ pkgs.python3Packages.hatchling ];
        nativeCheckInputs = [ pkgs.python3Packages.pytestCheckHook ];
        disabledTests = [
          # touches the real on-disk Brave profile, skipped via env in CI too
          "test_dump_real_profile_succeeds"
          "test_dry_run_apply_real_profile_does_not_write"
        ];
        meta = with pkgs.lib; {
          description = "Manage browser settings as dotfiles (Brave, Vivaldi, Edge)";
          homepage = "https://github.com/xom11/dotbrowser";
          license = licenses.mit;
          mainProgram = "dotbrowser";
          platforms = platforms.unix ++ platforms.windows;
        };
      };
    in
    {
      overlays.default = final: _prev: {
        dotbrowser = mkDotbrowser final;
      };

      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          dotbrowser = mkDotbrowser pkgs;
        in {
          inherit dotbrowser;
          default = dotbrowser;
        });

      devShells = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python3.withPackages (ps: [ ps.pytest ]))
            ];
          };
        });
    };
}
