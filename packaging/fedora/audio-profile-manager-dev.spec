Name:           audio-profile-manager-dev
Version:        0.5.0
Release:        2%{?dist}
Summary:        Multi-output audio routing profile manager
License:        MIT
URL:            https://github.com/varunsukumar060/Audio-Profile-Manager
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3
BuildRequires:  python3-devel
BuildRequires:  desktop-file-utils

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       pipewire-utils
Requires:       pulseaudio-utils

%description
Audio Profile Manager is a GTK application for managing saved audio output
profiles and routing audio through PipeWire-compatible audio sinks.

This package is the isolated development build. It uses the development
configuration directory and a separate desktop launcher.

%prep
%setup -q -n %{name}-%{version}

%build
# The application is written in Python and requires no compilation step.

%check
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" %{python3} -m unittest discover -s tests -v
desktop-file-validate packaging/fedora/audio-profile-manager-dev.desktop
PYTHONPYCACHEPREFIX="%{_builddir}/pycache" %{python3} -m compileall -q multi_output

%install
install -d %{buildroot}%{_datadir}/audio-profile-manager-dev
cp -a multi_output %{buildroot}%{_datadir}/audio-profile-manager-dev/

find %{buildroot}%{_datadir}/audio-profile-manager-dev \
    -type d -name __pycache__ -prune -exec rm -rf {} +
find %{buildroot}%{_datadir}/audio-profile-manager-dev \
    -type f -name '*.py[co]' -delete

install -D -m 0755 packaging/fedora/audio-profile-manager-dev-launcher \
    %{buildroot}%{_bindir}/audio-profile-manager-dev

install -D -m 0644 packaging/fedora/audio-profile-manager-dev.desktop \
    %{buildroot}%{_datadir}/applications/audio-profile-manager-dev.desktop

%files
%license LICENSE
%doc README.md
%{_bindir}/audio-profile-manager-dev
%{_datadir}/applications/audio-profile-manager-dev.desktop
%{_datadir}/audio-profile-manager-dev/

%changelog
* Sun Oct 25 2026 Audio Profile Manager contributors - 0.5.0-1
- Add initial Fedora development RPM packaging.
