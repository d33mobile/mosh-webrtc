/*
    Mosh: the mobile shell
    Copyright 2012 Keith Winstein

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.

    In addition, as a special exception, the copyright holders give
    permission to link the code of portions of this program with the
    OpenSSL library under certain conditions as described in each
    individual source file, and distribute linked combinations including
    the two.

    You must obey the GNU General Public License in all respects for all
    of the code used other than OpenSSL. If you modify file(s) with this
    exception, you may extend this exception to your version of the
    file(s), but you are not obligated to do so. If you do not wish to do
    so, delete this exception statement from your version. If you delete
    this exception statement from all source files in the program, then
    also delete it here.
*/

#ifndef WEBRTCBRIDGE_HPP
#define WEBRTCBRIDGE_HPP

#include <condition_variable>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include <netinet/in.h>
#include <sys/socket.h>

namespace rtc {
class PeerConnection;
class DataChannel;
}

namespace Network {

/* Pumps datagrams between a loopback UDP socket and a WebRTC data
   channel, so that Network::Connection keeps talking plain UDP to
   127.0.0.1 while the packets actually cross NATs via ICE/DTLS/SCTP. */
class WebRTCBridge
{
public:
  explicit WebRTCBridge( bool offerer );
  ~WebRTCBridge();

  /* Blocks until ICE gathering is complete (no trickle), returns base64 SDP. */
  std::string local_description( void );
  void set_remote_description( const std::string& base64_sdp );
  bool wait_open( unsigned int ms );

  /* Local port of the loopback socket, as a string usable by Connection. */
  std::string port( void ) const;

  /* Where to deliver channel messages before the loopback peer has sent
     anything (the server side learns the client's port this way). */
  void set_peer( const struct sockaddr* addr, socklen_t len );

  /* Launches the socket reader thread; call after any fork(). */
  void start( void );

  std::string selected_candidate_pair( void );

  WebRTCBridge( const WebRTCBridge& ) = delete;
  WebRTCBridge& operator=( const WebRTCBridge& ) = delete;

private:
  bool offerer;
  std::shared_ptr<rtc::PeerConnection> pc;
  std::shared_ptr<rtc::DataChannel> dc;

  int sock;
  struct sockaddr_in peer;
  bool peer_known;

  std::mutex mutex;
  std::condition_variable cond;
  bool gathering_complete;
  bool channel_open;

  std::thread reader;
  bool stop_reader;

  void attach_channel( std::shared_ptr<rtc::DataChannel> channel );
  void reader_loop( void );
  void deliver( const unsigned char* data, size_t len );
};
}

#endif
